from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import contextvars
import copy
from datetime import datetime, timezone

from jsonschema import validate

from .index import MemoryIndex
from .context import (
    estimate,
    completed_tail,
    fit_event,
    addressed_elsewhere,
    observe,
    eligible,
    apply_policy,
)
from .media import MediaStore
from .store import dump
from .tools import TOOLS, FINISH, POLICY, SYSTEM
from .types import Event, Scope
from .management import MemoryManagement, WRITES


class Engine:
    """按群协调触发、工具调用、发送和记忆写入。SQLite 保存可恢复状态。"""

    def __init__(self, store, gateway, platform, settings, clock):
        self.store, self.gateway, self.platform = store, gateway, platform
        self._settings, self.clock = settings, clock
        # 热更新只影响下一批；运行中的任务保持自己的配置和记忆版本。
        self.task_settings = contextvars.ContextVar('autochat_settings', default=None)
        self.task_revision = contextvars.ContextVar('autochat_revision', default=None)
        self.management = MemoryManagement(store)
        self.management_locks = {}
        self.index_tasks = set()
        self.media = MediaStore(store, settings, clock)
        self.index = MemoryIndex(store, gateway, settings.embedding_model, clock)
        self.tasks = {}
        self.summary_tasks = {}
        self.enabled = {}
        self.ingestions = {}
        self.last_media_cleanup = 0
        self.media.release_contexts = self.release_idle_contexts
        self.store.recover_actions()
        for scope in store.scopes():
            self.invalidate_batches(scope)
            state = store.state(scope)
            calls = {}
            for message in state["context"]:
                for call in message.get("tool_calls", []):
                    calls[call["id"]] = call
                if message["role"] == "tool":
                    calls.pop(message['tool_call_id'], None)
            for call_id, call in calls.items():
                state["context"].append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        'name': call['function']['name'],
                        "content": dump(
                            {
                                "error": "interrupted; consult actual action records, do not blindly resend"
                            }
                        ),
                    }
                )
            store.save_state(scope, state)

    def trace(self, scope, kind, value):
        self.store.trace(self.clock.now(), scope, kind, value)

    @property
    def settings(self):
        return self.task_settings.get() or self._settings

    def persona(self, scope):
        return self.settings.personas.get(scope.group_id, self.settings.persona)

    def update_settings(self, settings):
        self._settings = settings
        self.media.settings = settings
        if settings.embedding_model != self.index.model:
            self.index = MemoryIndex(self.store, self.gateway, settings.embedding_model, self.clock)

    def assert_revision(self, scope):
        revision = self.task_revision.get()
        if revision is not None and revision != self.store.revision(scope):
            raise asyncio.CancelledError('Memory changed during generation')

    def invalidate_batches(self, scope):
        """人工修改后清理旧草稿，保留抽样结果和已产生的平台动作。"""
        for row in self.store.db.execute(
            "SELECT id,value FROM batches WHERE scope=? AND status IN ('pending','running')",
            (scope.key,),
        ).fetchall():
            batch_id, decision = row['id'], json.loads(row['value'])
            if decision.get('memory_revision', 0) >= self.store.revision(scope):
                continue
            decision['memory_revision'] = self.store.revision(scope)
            actions = self.store.db.execute(
                'SELECT id,state FROM actions WHERE scope=? AND id LIKE ?',
                (scope.key, batch_id + ':%'),
            ).fetchall()
            committed = any(a['state'] in ('sent', 'sending', 'unknown') for a in actions)
            for action in actions:
                if action['state'] == 'pending':
                    self.store.finish_action(
                        action['id'], 'cancelled', {'reason': 'manual_memory_change'}
                    )
            if committed:
                self.store.batch_done(batch_id, 'finished')
                self.store.mark_handled(self.store.events(scope, ids=decision['message_ids']))
            else:
                decision['generation'] = decision.get('generation', 0) + 1
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE batches SET value=?,status='pending' WHERE id=?",
                        (dump(decision), batch_id),
                    )
            with self.store.db:
                self.store.db.execute(
                    'DELETE FROM kv WHERE key IN (?,?)',
                    ('commit:' + batch_id, 'result:' + batch_id),
                )

    async def manage_memory(self, request):
        """先原子提交人工结果，再取消旧任务；重复命令直接返回原结果。"""
        scope = Scope(str(request['bot_id']), str(request['group_id']))
        command, admin = request['command'], request.get('admin') is True
        if command['op'] not in WRITES:
            return self.management.query(scope, command, admin)
        if not admin:
            raise PermissionError('Memory write requires group administrator permission')
        lock = self.management_locks.setdefault(scope.key, asyncio.Lock())
        async with lock:
            before = self.store.revision(scope)
            result = self.management.mutate(
                scope,
                command,
                str(request['actor_id']),
                str(request['message_id']),
                self.clock.now(),
            )
            if before == self.store.revision(scope):
                return result
            workers = [
                tasks[scope.key] for tasks in (self.tasks, self.summary_tasks) if scope.key in tasks
            ]
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self.tasks.pop(scope.key, None)
            self.summary_tasks.pop(scope.key, None)
            self.invalidate_batches(scope)

            # 索引是可重建的派生数据。后台更新失败不撤销已提交的人工操作。
            async def update_index():
                try:
                    await self.index.sync(scope)
                    self.store.set('index_dirty:' + scope.key, False)
                except Exception as exc:
                    self.index.degraded(scope, exc, 'text_search')

            task = asyncio.create_task(update_index())
            self.index_tasks.add(task)
            task.add_done_callback(self.index_tasks.discard)
            return result

    async def ingest(self, value):
        event = Event.from_dict(value)
        existing = self.store.events(event.scope, ids=[event.message_id])
        if existing and not any(s.get('data', {}).get('pending') for s in existing[0].segments):
            return False
        future = asyncio.get_running_loop().create_future()
        self.ingestions.setdefault(event.scope.key, set()).add(future)
        original = Event.from_dict(value)
        for segment in event.segments:
            if segment['type'] == 'image':
                segment['data'] = {'pending': True, 'unavailable': '图片正在下载'}
        if existing:
            event.seq = existing[0].seq
        else:
            self.store.add_event(event, self.clock.now())
            self.trace(event.scope, "received", {"message_id": event.message_id, "seq": event.seq})
        ingested = set()
        try:
            for segment, source_segment in zip(event.segments, original.segments):
                if segment.get("type") != "image":
                    continue
                data = {}
                try:
                    data["asset_id"] = await self.media.ingest(
                        source_segment['data'].get('url', '')
                    )
                    if data['asset_id'] not in ingested:
                        ingested.add(data['asset_id'])
                        self.media.pin([data['asset_id']])
                except Exception as exc:
                    data["unavailable"] = str(exc)
                    self.trace(
                        event.scope,
                        "media_error",
                        {"message_id": event.message_id, "error": str(exc)},
                    )
                segment["data"] = data
            self.store.update_event(event)
            return not bool(existing)
        finally:
            self.media.unpin(ingested)
            future.set_result(None)
            self.ingestions[event.scope.key].discard(future)

    def release_idle_contexts(self):
        for scope in self.store.scopes():
            if scope.key in self.tasks and not self.tasks[scope.key].done():
                continue
            state = self.store.state(scope)
            if state['context']:
                self.store.set('retired_context:' + scope.key, state['context'])
                state['context'], state['visible_sources'] = [], []
                self.store.save_state(scope, state)
                self.trace(scope, 'context_retired', {'reason': 'media_quota'})

    def direct(self, event):
        for segment in event.segments:
            data = segment.get("data", {})
            if segment.get("type") == "at" and str(data.get("qq")) == event.bot_id:
                return True
            if segment.get("type") == "reply":
                targets = self.store.events(
                    event.scope, ids=[str(data.get("id"))], now=self.clock.now()
                )
                if targets and targets[0].speaker_id == event.bot_id:
                    return True
        return False

    def policy(self, state):
        value = state.get("policy", {})
        if value.get("expires_at", 0) <= self.clock.now():
            return {
                "ambient_p": self.settings.ambient_p,
                "followup_p": self.settings.followup_p,
                "focus_user_ids": [],
                "ttl_seconds": self.settings.policy_ttl,
            }
        return {
            **value,
            'focus_user_ids': [
                uid
                for uid in value.get('focus_user_ids', [])
                if uid in eligible(state, self.clock.now())
            ],
        }

    def budget(self, scope, category, limit, window, consume=False):
        state = self.store.state(scope)
        times = [t for t in state[category] if t > self.clock.now() - window]
        if len(times) >= limit:
            return False
        if consume:
            times.append(self.clock.now())
            state[category] = times
            self.store.save_state(scope, state)
        return True

    async def tick(self):
        if self.clock.now() - self.last_media_cleanup >= 600:
            self.media.clean()
            self.last_media_cleanup = self.clock.now()
        for workers in (self.tasks, self.summary_tasks):
            for key, task in list(workers.items()):
                if task.done():
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        self.trace(
                            Scope(*key.split(":", 1)),
                            "worker_error",
                            {"type": type(exc).__name__, "error": str(exc)[:500]},
                        )
                    del workers[key]
        for scope in self.store.scopes():
            if self.management_locks.get(scope.key) and self.management_locks[scope.key].locked():
                continue
            if not self.enabled.get(scope.key, True):
                if scope.key in self.summary_tasks:
                    self.summary_tasks[scope.key].cancel()
                pending = self.store.events(scope, pending=True, limit=10000)
                self.store.mark_handled(pending)
                self.store.db.execute(
                    "UPDATE batches SET status='cancelled' WHERE scope=? AND status IN ('pending','running')",
                    (scope.key,),
                )
                self.store.db.commit()
                continue
            if scope.key not in self.summary_tasks and self.summary_due(scope):
                self.summary_tasks[scope.key] = asyncio.create_task(self.consolidate(scope))
            if scope.key in self.tasks:
                continue
            events = self.store.events(scope, pending=True, now=self.clock.now(), limit=100)
            ignore = [
                e for e in events if e.speaker_id == scope.bot_id or e.text.strip().startswith("/")
            ]
            self.store.mark_handled(ignore)
            events = [e for e in events if e not in ignore]
            if not events:
                continue
            unfinished = self.store.db.execute(
                "SELECT id,value FROM batches WHERE scope=? AND status IN ('pending','running') ORDER BY rowid LIMIT 1",
                (scope.key,),
            ).fetchone()
            if unfinished:
                saved = json.loads(unfinished["value"])
                events = self.store.events(
                    scope, ids=saved["message_ids"], now=self.clock.now(), limit=100
                )
                if not events:
                    self.store.batch_done(unfinished['id'], 'cancelled')
                    continue
            else:
                rows = self.store.db.execute(
                    "SELECT MIN(received),MAX(received) FROM events WHERE seq IN ("
                    + ",".join("?" for _ in events)
                    + ")",
                    [e.seq for e in events],
                ).fetchone()
                if self.clock.now() < min(
                    rows[0] + self.settings.max_batch_wait, rows[1] + self.settings.debounce
                ):
                    continue
            if not unfinished and not self.budget(
                scope, "wakes", self.settings.wakes_per_minute, 60
            ):
                continue
            state = observe(self, scope, events)
            policy = self.policy(state)
            direct = any(self.direct(e) for e in events)
            followup = any(
                e.speaker_id in policy["focus_user_ids"] and not addressed_elsewhere(self, e)
                for e in events
            )
            probability = (
                1.0 if direct else policy["followup_p"] if followup else policy["ambient_p"]
            )
            reason = 'direct' if direct else 'followup' if followup else 'ambient'
            seed = dump([self.settings.seed, scope.key, [e.message_id for e in events]])
            draw = int(hashlib.sha256(seed.encode()).hexdigest()[:13], 16) / 16**13
            batch_id, decision, status = self.store.batch(
                scope,
                events,
                {
                    "memory_revision": self.store.revision(scope),
                    "draw": draw,
                    "probability": probability,
                    "wake": draw < probability,
                    "reason": reason,
                    "message_ids": [e.message_id for e in events],
                },
            )
            self.trace(scope, "trigger", {"batch_id": batch_id, **decision})
            if not decision["wake"]:
                self.store.mark_handled(events)
                self.store.batch_done(batch_id, "silent")
            else:
                if not unfinished:
                    self.budget(scope, "wakes", self.settings.wakes_per_minute, 60, True)
                self.store.batch_done(batch_id, "running")
                self.tasks[scope.key] = asyncio.create_task(self.turn(scope, events, batch_id))

    def event_message(self, event):
        timestamp = datetime.fromtimestamp(event.time, timezone.utc).isoformat()
        parts = [
            {
                "type": "text",
                "text": f"[{timestamp}] message_id={event.message_id} speaker_id={event.speaker_id} nickname={json.dumps(event.nickname, ensure_ascii=False)}\n",
            }
        ]
        overrides = self.store.source_overrides(event.scope, event.message_id)
        if overrides:
            parts.append(
                {
                    'type': 'text',
                    'text': 'memory_overrides（以下来源片段已人工更正或撤销，不可作为当前事实）：'
                    + dump(overrides),
                }
            )
        for segment in event.segments:
            kind, data = segment.get("type"), segment.get("data", {})
            if kind == "text":
                parts.append({"type": "text", "text": data.get("text", "")})
            elif kind == "image" and data.get("asset_id"):
                parts.append(
                    {
                        "type": "text",
                        "text": f"[附件状态=已加载 图片来源={event.message_id} asset_id={data['asset_id']}]",
                    }
                )
                parts.append({"type": "image_ref", "asset_id": data["asset_id"]})
            elif kind == "image":
                parts.append(
                    {
                        "type": "text",
                        "text": "[附件状态=不可用；图片不可用，未查看原图。前面的文字仅是发送者文字/描述，不包含可见原图。]",
                    }
                )
            else:
                parts.append({"type": "text", "text": f"[{kind}:{dump(data)}]"})
        return {"role": "user", "content": parts}

    def append_context(self, scope, messages):
        state = self.store.state(scope)
        state["context"].extend(messages)
        self.store.save_state(scope, state)

    def input_budget(self, model):
        spec = getattr(self.gateway, 'models', {}).get(model)
        window = spec.context_window if spec else 32768
        # Reserve space for the fixed tool declarations and provider framing.
        return (
            min(self.settings.input_tokens, window - self.settings.output_tokens)
            - len(dump(TOOLS).encode('utf-8'))
            - 256
        )

    async def prepare_context(self, scope, force=False, model=None, required_ids=()):
        pending_media = list(self.ingestions.get(scope.key, ()))
        if pending_media:
            await asyncio.gather(*(asyncio.shield(future) for future in pending_media))
        state = self.store.state(scope)
        events = self.store.events(
            scope, after=state["context_cursor"], now=self.clock.now(), limit=2000
        )
        required = self.store.events(scope, ids=list(required_ids), now=self.clock.now(), limit=100)
        by_id = {e.message_id: e for e in events}
        by_id.update(
            (e.message_id, e) for e in required if e.message_id not in state['visible_sources']
        )
        events = sorted(by_id.values(), key=lambda e: e.seq)
        events = [e for e in events if not e.text.strip().startswith("/")]
        selected = model or state.get('model') or self.settings.model
        if selected not in [self.settings.model, *self.settings.fallback_models]:
            selected = self.settings.model
        spec = getattr(self.gateway, 'models', {}).get(selected)
        signature = hashlib.sha256(
            dump(
                [
                    selected,
                    spec.name if spec else None,
                    spec.protocol if spec else None,
                    self.persona(scope),
                    SYSTEM,
                    TOOLS,
                ]
            ).encode()
        ).hexdigest()
        budget = self.input_budget(selected)
        if budget < 1800:
            raise ValueError(
                'Model context/input budget is too small for tools and system instructions'
            )
        reserve = self.settings.image_token_reserve
        if state.get('awaiting', {}).get('expires_at', 0) <= self.clock.now():
            state['awaiting'] = {}
        current = state['context'] + [self.event_message(e) for e in events]
        if (
            force
            or not state["context"]
            or state.get('model_signature') != signature
            or estimate(current, reserve) > budget * 0.85
        ):
            recent = self.store.events(scope, now=self.clock.now(), limit=20, latest=True)
            recent = [e for e in recent if not e.text.strip().startswith('/')]
            # Required delayed/out-of-order events get a place at the segment tail.
            recent = [e for e in recent if e.message_id not in required_ids] + required
            subjects = sorted({e.speaker_id for e in recent if e.speaker_id != scope.bot_id})[-4:]
            snapshot = (
                self.store.memories(scope, subjects, limit=8, now=self.clock.now())
                if subjects
                else []
            )
            # Compact only at a completed tool boundary; raw history remains queryable.
            old = state["context"] or self.store.get('retired_context:' + scope.key, [])
            summary = None
            if (
                old
                and self.settings.summary_model
                and self.budget(scope, "summary_calls", self.settings.summary_per_hour, 3600, True)
            ):
                try:
                    self.trace(
                        scope,
                        'aux_model_request',
                        {'task': 'compaction', 'model': self.settings.summary_model},
                    )
                    result = await self.gateway.query_llm(
                        self.settings.summary_model,
                        [
                            {
                                "role": "system",
                                "content": "压缩对话，保留用户ID、来源消息ID、未完成约定、实际发送结果和不确定性；不要把工具调用意图当成成功。",
                            },
                            {
                                "role": "user",
                                "content": dump(completed_tail(old, max(1800, budget // 2))),
                            },
                        ],
                        options={"max_tokens": 1200},
                    )
                    summary = result.assistant_message.get("content")
                    self.trace(scope, "model_usage", {"task": "compaction", "usage": result.usage})
                except Exception as exc:
                    self.trace(scope, "compaction_error", {"type": type(exc).__name__})
            state = self.store.state(scope)
            if state.get('awaiting', {}).get('expires_at', 0) <= self.clock.now():
                state['awaiting'] = {}
            state["epoch"] += 1
            state['model'] = selected
            state['model_signature'] = signature
            state['visible_sources'] = []
            actions = [
                dict(r)
                for r in self.store.db.execute(
                    'SELECT id,state,result FROM actions WHERE scope=? ORDER BY rowid DESC LIMIT 4',
                    (scope.key,),
                )
            ]
            state["context"] = [
                {
                    "role": "system",
                    "content": SYSTEM + "\n人设：" + self.persona(scope) + "\n身份：" + scope.key,
                },
                {
                    "role": "user",
                    "content": "冻结记忆快照："
                    + dump(snapshot)
                    + "\n未完成等待："
                    + dump(state.get("awaiting", {}))
                    + "\n实际动作："
                    + dump(actions)
                    + "\n前段摘要："
                    + str(summary or "早期原文已移出工作上下文，可通过工具读取。")[:1800],
                },
            ]
            while snapshot and estimate(state['context'], reserve) > budget // 2:
                snapshot.pop()
                state['context'][1]['content'] = (
                    '冻结记忆快照：'
                    + dump(snapshot)
                    + '\n实际动作：'
                    + dump(actions)
                    + '\n未完成等待：'
                    + dump(state.get('awaiting', {}))
                )
            target = int(budget * 0.55)
            required_size = sum(estimate([self.event_message(e)], reserve) for e in required)
            fixed_size = estimate(state['context'], reserve)
            target = min(budget - 1200, max(target, fixed_size + required_size))
            kept, remaining = [], target - fixed_size
            for event in reversed(recent):
                message = self.event_message(event)
                if not kept:
                    message = fit_event(message, max(500, remaining), reserve)
                size = estimate([message], reserve)
                if size > remaining:
                    break
                kept.insert(0, (event, message))
                remaining -= size
            omitted_required = set(required_ids) - {event.message_id for event, _ in kept}
            if omitted_required:
                # Every current source keeps an identifiable, explicitly truncated
                # representation. Full originals remain available to read_messages.
                allowance = max(300, (budget - 1200 - fixed_size) // max(1, len(required)))
                kept = [
                    (event, fit_event(self.event_message(event), allowance, reserve))
                    for event in required
                ]
                if (
                    fixed_size + sum(estimate([message], reserve) for _, message in kept)
                    > budget - 600
                ):
                    raise ValueError('Required source headers exceed the input budget')
            events = []
            for event, message in kept:
                state['context'].append(message)
                state['visible_sources'].append(event.message_id)
            if recent:
                state['context_cursor'] = max(state['context_cursor'], recent[-1].seq)
            self.store.set('retired_context:' + scope.key, [])
            self.trace(
                scope,
                "context_epoch",
                {
                    "epoch": state["epoch"],
                    "model": selected,
                    "summary": bool(summary),
                    'estimated_size': estimate(state['context'], reserve),
                    'target': int(budget * 0.55),
                    'required_exceeds_target': fixed_size + required_size > int(budget * 0.55),
                    'required_truncated_to_input_budget': bool(omitted_required),
                },
            )
        for event in events:
            state["context"].append(self.event_message(event))
            state["context_cursor"] = max(state["context_cursor"], event.seq)
            state['visible_sources'].append(event.message_id)
        # Current state belongs at the tail, never in the frozen system prefix.
        participants = sorted(
            {e.speaker_id for e in [*events, *required] if e.speaker_id != scope.bot_id}
        )[-4:]
        facts = (
            self.store.memories(scope, participants, limit=4, now=self.clock.now())
            if participants
            else []
        )
        candidates = (
            self.store.memories(
                scope,
                participants,
                statuses=('candidate', 'pending_review'),
                limit=4,
                now=self.clock.now(),
            )
            if participants
            else []
        )
        while facts and len(dump(facts).encode('utf-8')) > 2500:
            facts.pop()
        state["context"].append(
            {
                "role": "user",
                "content": dump(
                    {
                        "now": self.clock.now(),
                        "trigger_policy": self.policy(state),
                        "current_user_facts": facts,
                        'unverified_candidates': candidates,
                        'unanswered_ambient_turns': state['unanswered'],
                        'awaiting': state.get('awaiting', {}),
                        'processing_message_ids': list(required_ids),
                    }
                ),
            }
        )
        self.store.save_state(scope, state)
        self.media.clean()

    async def model_messages(self, scope, model, context):
        spec = getattr(self.gateway, 'models', {}).get(model)
        if spec and not spec.multimodal:
            import copy

            context = copy.deepcopy(context)
            for message in context:
                if not isinstance(message.get('content'), list):
                    continue
                for part in message['content']:
                    if part.get('type') != 'image_ref':
                        continue
                    asset_id = part['asset_id']
                    key = 'vision:' + self.settings.vision_model + ':' + asset_id
                    description = self.store.get(key)
                    if description is None and self.settings.vision_model:
                        self.trace(
                            scope,
                            'aux_model_request',
                            {'task': 'vision_fallback', 'model': self.settings.vision_model},
                        )
                        result = await self.gateway.query_llm(
                            self.settings.vision_model,
                            self.media.materialize(
                                [
                                    {
                                        'role': 'user',
                                        'content': [
                                            {
                                                'type': 'text',
                                                'text': '描述图片中可见内容，不推断上传者身份或把图中事实归给上传者。',
                                            },
                                            part,
                                        ],
                                    }
                                ]
                            ),
                            options={'max_tokens': 800},
                        )
                        description = (
                            result.assistant_message.get('content') or '[描述模型未返回文字]'
                        )
                        self.store.set(key, description)
                        self.trace(
                            scope, 'model_usage', {'task': 'vision_fallback', 'usage': result.usage}
                        )
                    part.clear()
                    part.update(
                        type='text',
                        text='[图片描述降级；主模型未看原图] '
                        + str(description or '未配置 vision_model，图片不可见'),
                    )
        return self.media.materialize(context)

    async def turn(self, scope, events, batch_id):
        """执行一个已抽样批次；仅 finish_turn 可以提交对外发送。"""
        self.task_settings.set(copy.deepcopy(self._settings))
        self.task_revision.set(self.store.revision(scope))
        started = time.monotonic()
        consumed = list(events)
        watermark = max(e.seq for e in events)
        authors = {e.speaker_id for e in events}
        rebased = False
        interrupted = False
        corrections = 0
        completed = None
        try:
            await self.prepare_context(scope, required_ids=[e.message_id for e in consumed])
            visible = set(self.store.state(scope)['visible_sources'])
            committed = self.store.get('commit:' + batch_id)
            if committed:
                # Resume only the persisted intent. Stable action IDs suppress sent/unknown actions.
                await self.finish(
                    scope,
                    batch_id,
                    committed['args'],
                    set(committed['visible']),
                    watermark,
                    authors,
                )
                self.store.batch_done(batch_id, 'finished')
                return
            for round_index in range(self.settings.max_rounds):
                if not self.enabled.get(scope.key, True):
                    break
                if not self.budget(scope, "calls", self.settings.calls_per_minute, 60, True):
                    self.trace(scope, "budget_exhausted", {"batch_id": batch_id})
                    break
                state = self.store.state(scope)
                model, context = state['model'], state['context']
                if estimate(context, self.settings.image_token_reserve) > self.input_budget(model):
                    await self.prepare_context(
                        scope, force=True, required_ids=[e.message_id for e in consumed]
                    )
                    context = self.store.state(scope)['context']
                    visible = set(self.store.state(scope)['visible_sources'])
                if estimate(context, self.settings.image_token_reserve) > self.input_budget(model):
                    raise ValueError('Context exceeds configured input budget after compaction')
                # A read tool may already have delivered the correction to the
                # model. Only advance when every relevant update is visible;
                # a newer unseen message must still invalidate the draft.
                updates = self.relevant_updates(scope, watermark, authors)
                if updates and all(e.message_id in visible for e in updates):
                    known_consumed = {e.message_id for e in consumed}
                    consumed.extend(e for e in updates if e.message_id not in known_consumed)
                    observe(self, scope, updates)
                    watermark = max(e.seq for e in updates)
                    self.trace(
                        scope,
                        'updates_in_context',
                        {'batch_id': batch_id, 'new_messages': [e.message_id for e in updates]},
                    )
                request = await self.model_messages(scope, model, context)
                self.trace(
                    scope,
                    "model_request",
                    {
                        "batch_id": batch_id,
                        "round": round_index,
                        "model": model,
                        "prefix_hash": hashlib.sha256(dump(context).encode()).hexdigest(),
                        "input_estimate": estimate(context, self.settings.image_token_reserve),
                    },
                )
                try:
                    result = await self.gateway.query_llm(
                        model,
                        request,
                        TOOLS,
                        {
                            "max_tokens": self.settings.output_tokens,
                            "timeout": self.settings.timeout,
                        },
                    )
                    self.assert_revision(scope)
                except Exception as exc:
                    self.trace(scope, 'model_error', {'model': model, 'type': type(exc).__name__})
                    chain = [self.settings.model, *self.settings.fallback_models]
                    index = chain.index(model) if model in chain else len(chain)
                    if index + 1 >= len(chain):
                        raise
                    await self.prepare_context(
                        scope,
                        force=True,
                        model=chain[index + 1],
                        required_ids=[e.message_id for e in consumed],
                    )
                    visible = set(self.store.state(scope)['visible_sources'])
                    continue
                self.trace(
                    scope,
                    "model_usage",
                    {"task": "chat", "usage": result.usage, "finish_reason": result.finish_reason},
                )
                # Audit survives later context rebuilding, including native Gemini
                # signatures/call IDs. It is not fed back as an extra prompt copy.
                self.trace(
                    scope,
                    'model_response',
                    {
                        'batch_id': batch_id,
                        'round': round_index,
                        'model': model,
                        'assistant_message': result.assistant_message,
                    },
                )
                self.append_context(scope, [result.assistant_message])
                calls = result.tool_calls
                if not calls:
                    if corrections:
                        break
                    corrections += 1
                    self.append_context(
                        scope,
                        [
                            {
                                "role": "user",
                                "content": "请调用 finish_turn 提交或沉默，普通文字不会发送。",
                            }
                        ],
                    )
                    continue
                reads = [c for c in calls if c["function"]["name"] != "finish_turn"]
                finishes = [c for c in calls if c["function"]["name"] == "finish_turn"]
                if reads:
                    results = await asyncio.gather(
                        *(
                            self.read_tool(scope, call, visible)
                            for call in reads[: self.settings.max_read_calls]
                        )
                    )
                    by_id = {call["id"]: result for call, result in zip(reads, results)}
                    attachments = []
                    for call in calls:
                        value = by_id.get(
                            call["id"],
                            {
                                "error": "finish_turn must be separate from reads; or read limit exceeded"
                            },
                        )
                        if isinstance(value, dict) and value.get("attachment"):
                            attachments.append(value.pop("attachment"))
                            if value.get('image'):
                                attachments.append(value.pop('image'))
                        self.tool_result(scope, call, value)
                    if attachments:
                        self.append_context(scope, [{"role": "user", "content": attachments}])
                        self.media.unpin(
                            p['asset_id'] for p in attachments if p.get('type') == 'image_ref'
                        )
                    st = self.store.state(scope)
                    st['visible_sources'] = sorted(visible)
                    self.store.save_state(scope, st)
                    continue
                newer = self.store.events(
                    scope, after=watermark, now=self.clock.now(), pending=True, limit=100
                )
                focus = set(self.policy(self.store.state(scope))["focus_user_ids"])
                relevant = self.relevant_updates(scope, watermark, authors | focus)
                if relevant or not self.enabled.get(scope.key, True):
                    for call in calls:
                        self.tool_result(
                            scope,
                            call,
                            {
                                "error": "draft superseded by relevant new messages or group disabled; no actions executed"
                            },
                        )
                    self.trace(
                        scope,
                        "draft_cancelled",
                        {"batch_id": batch_id, "new_messages": [e.message_id for e in relevant]},
                    )
                    if rebased or not self.enabled.get(scope.key, True):
                        break
                    rebased = True
                    consumed.extend(relevant)
                    observe(self, scope, relevant)
                    watermark = max(e.seq for e in newer)
                    await self.prepare_context(scope, required_ids=[e.message_id for e in consumed])
                    visible = set(self.store.state(scope)['visible_sources'])
                    continue
                if len(finishes) != 1:
                    for call in finishes:
                        self.tool_result(
                            scope, call, {"error": "Exactly one finish_turn is allowed"}
                        )
                    continue
                call = finishes[0]
                try:
                    args = json.loads(call["function"]["arguments"])
                    validate(args, FINISH)
                    if completed is not None:
                        value = {
                            'memories': await self.propose_memories(
                                scope, args['memory_proposals'], visible
                            ),
                            'messages': [],
                            'note': 'memory review only; original messages and policy already committed',
                        }
                    else:
                        value = await self.finish(
                            scope, batch_id, args, visible, watermark, authors
                        )
                except Exception as exc:
                    self.tool_result(scope, call, {"error": str(exc)[:500]})
                    if corrections:
                        break
                    corrections += 1
                    continue
                self.tool_result(scope, call, value)
                if (
                    completed is None
                    and any(m.get('possible_duplicates') for m in value['memories'])
                    and round_index + 1 < self.settings.max_rounds
                ):
                    completed = value
                    self.append_context(
                        scope,
                        [
                            {
                                'role': 'user',
                                'content': '本轮发送和策略已完成。只在剩余预算内澄清刚才的 possible_duplicates；读取必要来源后用 duplicate_of 或 distinct_from。finish_turn.messages 必须为空。证据不足可保留待审，不再发言。',
                            }
                        ],
                    )
                    continue
                self.trace(
                    scope,
                    'turn_complete',
                    {
                        'batch_id': batch_id,
                        'sent_count': sum(
                            m['state'] == 'sent' for m in (completed or value)['messages']
                        ),
                        'latency_wall_seconds': time.monotonic() - started,
                    },
                )
                self.store.batch_done(batch_id, "finished")
                return
            self.store.batch_done(batch_id, "finished" if completed else "stopped")
            if completed:
                self.trace(
                    scope,
                    'turn_complete',
                    {
                        'batch_id': batch_id,
                        'sent_count': sum(m['state'] == 'sent' for m in completed['messages']),
                        'memory_review_deferred': True,
                    },
                )
        except asyncio.CancelledError:
            interrupted = True
            self.trace(scope, 'turn_interrupted', {'batch_id': batch_id})
            raise
        except Exception as exc:
            if completed:
                self.trace(
                    scope,
                    'memory_review_deferred',
                    {'batch_id': batch_id, 'type': type(exc).__name__},
                )
                self.trace(
                    scope,
                    'turn_complete',
                    {
                        'batch_id': batch_id,
                        'sent_count': sum(m['state'] == 'sent' for m in completed['messages']),
                        'memory_review_deferred': True,
                    },
                )
                self.store.batch_done(batch_id, 'finished')
            else:
                self.trace(
                    scope,
                    "turn_error",
                    {"batch_id": batch_id, "type": type(exc).__name__, "error": str(exc)[:500]},
                )
                self.store.batch_done(batch_id, "failed")
        finally:
            if not interrupted:
                self.store.mark_handled(consumed)

    def relevant_updates(self, scope, watermark, authors):
        focus = set(self.policy(self.store.state(scope))['focus_user_ids'])
        newer = self.store.events(
            scope, after=watermark, now=self.clock.now(), pending=True, limit=1000
        )
        return [
            e
            for e in newer
            if e.speaker_id != scope.bot_id
            and not e.text.strip().startswith('/')
            and (e.speaker_id in set(authors) | focus or self.direct(e))
        ]

    def tool_result(self, scope, call, value):
        content = dump(value)
        if len(content) > 16000:
            content = dump({"truncated": True, "excerpt": content[:15000]})
        self.append_context(
            scope,
            [
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": content,
                }
            ],
        )
        self.trace(
            scope,
            "tool_result",
            {"call_id": call["id"], "tool": call["function"]["name"], "result": value},
        )

    async def read_tool(self, scope, call, visible):
        try:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            schema = next(
                t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == name
            )
            validate(args, schema)
            if name == "read_messages":
                records = self.store.events(
                    scope,
                    before=args.get("before_seq"),
                    ids=args.get("message_ids"),
                    now=self.clock.now(),
                    latest=True,
                    limit=min(args.get("limit", 20), 30),
                )
                while records and len(dump([e.to_dict() for e in records])) > 14000:
                    records.pop(0)
                visible.update(e.message_id for e in records)
                return {
                    "messages": [
                        {
                            **e.to_dict(),
                            'memory_overrides': self.store.source_overrides(scope, e.message_id),
                        }
                        for e in records
                    ]
                }
            if name == "get_user_memory":
                statuses = (
                    ('active', 'candidate', 'pending_review')
                    if args.get('include_unverified')
                    else ('active',)
                )
                return {
                    "memories": self.store.memories(
                        scope, args["user_ids"], statuses=statuses, limit=20, now=self.clock.now()
                    )
                }
            if name == "search_memory":
                memories = await self.index.search(
                    scope,
                    args['query'],
                    args.get('subject_ids'),
                    args.get('include_unverified', False),
                )
                return {'memories': memories}
            if name == 'clarify_memories':
                return {'memories': await self.propose_memories(scope, args['proposals'], visible)}
            if name == "load_media":
                records = self.store.events(scope, ids=[args["message_id"]], now=self.clock.now())
                if not records or not any(
                    s.get("data", {}).get("asset_id") == args["asset_id"]
                    for s in records[0].segments
                ):
                    raise ValueError("Attachment does not belong to this message")
                visible.add(records[0].message_id)
                available = self.media.path(args["asset_id"]).exists()
                if available:
                    self.media.pin([args['asset_id']])
                return {
                    "available": available,
                    "message_id": args["message_id"],
                    "asset_id": args["asset_id"],
                    **(
                        {
                            "attachment": {
                                'type': 'text',
                                'text': f"[message_id={records[0].message_id} speaker_id={records[0].speaker_id}]",
                            },
                            "image": {"type": "image_ref", "asset_id": args["asset_id"]},
                        }
                        if available
                        else {}
                    ),
                }
            raise ValueError("Unknown tool")
        except Exception as exc:
            return {"error": str(exc)[:500]}

    async def propose_memories(self, scope, proposals, visible):
        results = []
        for proposal in proposals:
            try:
                neighbors = (
                    []
                    if proposal.get('duplicate_of')
                    else await self.index.duplicates(scope, proposal)
                )
                self.assert_revision(scope)
                results.append(
                    self.store.propose(scope, proposal, self.clock.now(), visible, neighbors)
                )
            except Exception as exc:
                results.append({'status': 'rejected', 'error': str(exc)})
        return results

    async def finish(self, scope, batch_id, args, visible, watermark=0, authors=()):
        self.assert_revision(scope)
        existing = self.store.get('result:' + batch_id)
        if existing is not None:
            return existing
        policy = dict(args["next_trigger"])
        validate(policy, POLICY)
        if not all(math.isfinite(policy[k]) for k in ("ambient_p", "followup_p", "ttl_seconds")):
            raise ValueError("Non-finite policy")
        known_events = self.store.events(
            scope, ids=list(visible), now=self.clock.now(), limit=100000
        )
        known_users = {e.speaker_id for e in known_events}
        if not set(policy["focus_user_ids"]).issubset(known_users):
            raise ValueError("Focus user was not visible")
        if len(args["messages"]) > self.settings.max_messages:
            raise ValueError("Too many messages")
        prepared = []
        for message in args["messages"]:
            if not message["text"].strip() or len(message["text"]) > self.settings.reply_max_length:
                raise ValueError("Invalid reply length")
            if not set(
                message.get("at_user_ids", []) + message.get("awaiting_user_ids", [])
            ).issubset(known_users):
                raise ValueError("Unknown reply target")
            if message.get("reply_to_message_id") and message["reply_to_message_id"] not in visible:
                raise ValueError("Unknown quoted message")
            segments = []
            if message.get("reply_to_message_id"):
                segments.append({"type": "reply", "data": {"id": message["reply_to_message_id"]}})
            segments.extend(
                {"type": "at", "data": {"qq": uid}} for uid in message.get("at_user_ids", [])
            )
            segments.append({"type": "text", "data": {"text": message["text"]}})
            prepared.append((message, segments))
        self.store.set('commit:' + batch_id, {'args': args, 'visible': sorted(visible)})
        sent = []
        for index, (message, segments) in enumerate(prepared):
            batch_row = self.store.db.execute(
                'SELECT value FROM batches WHERE id=?', (batch_id,)
            ).fetchone()
            decision = json.loads(batch_row[0]) if batch_row else {}
            action_id = f"{batch_id}:{decision.get('generation', 0)}:{index}"
            action = self.store.start_action(
                action_id, scope, {"segments": segments, "message": message}
            )
            if action["state"] != "pending":
                sent.append(
                    {"action_id": action_id, "state": action["state"], "result": action["result"]}
                )
                continue
            if not self.enabled.get(scope.key, True) or (
                watermark and self.relevant_updates(scope, watermark, authors)
            ):
                result = {'reason': 'disabled_or_new_messages'}
                self.store.finish_action(action_id, "cancelled", result)
                sent.append({'action_id': action_id, 'state': 'cancelled', 'result': result})
                self.trace(scope, 'send', sent[-1])
                continue
            self.store.finish_action(action_id, "sending", {})
            try:
                self.assert_revision(scope)
                # Use the original persisted intent if a process restarted mid-turn.
                response = await self.platform.send(scope, action["payload"]["segments"], action_id)
                state = response.get("state", "sent" if response.get("message_id") else "unknown")
            except asyncio.CancelledError:
                self.store.finish_action(
                    action_id, 'unknown', {'reason': 'interrupted_during_send'}
                )
                raise
            except Exception as exc:
                state, response = "unknown", {"error": type(exc).__name__}
            if state not in ("sent", "failed", "unknown", "cancelled"):
                state = "unknown"
            self.store.finish_action(action_id, state, response)
            sent.append({"action_id": action_id, "state": state, "result": response})
            self.trace(scope, "send", sent[-1])
            self.assert_revision(scope)
            if state == "sent" and response.get("message_id"):
                event = Event(
                    scope.bot_id,
                    scope.group_id,
                    str(response["message_id"]),
                    scope.bot_id,
                    self.clock.now(),
                    action["payload"]["segments"],
                    "bot",
                )
                stored = self.store.add_event(event, self.clock.now())
                if stored:
                    self.store.mark_handled([stored])
                waiting = action["payload"]["message"].get("awaiting_user_ids", [])
                if waiting and any(mark in message['text'] for mark in ('?', '？', '吗', '呢')):
                    st = self.store.state(scope)
                    # An unanswered question cannot renew its own attention lease.
                    if not st.get('awaiting') and st['unanswered'] == 0:
                        st["awaiting"] = {
                            "message_id": event.message_id,
                            "user_ids": waiting,
                            "expires_at": self.clock.now()
                            + min(policy["ttl_seconds"], self.settings.attention_seconds),
                        }
                        self.store.save_state(scope, st)
        # Replies are delivered before optional vector deduplication/network work.
        memories = await self.propose_memories(scope, args['memory_proposals'], visible)
        st = self.store.state(scope)
        batch = self.store.db.execute(
            'SELECT value FROM batches WHERE id=?', (batch_id,)
        ).fetchone()
        if (
            batch
            and json.loads(batch['value']).get('reason') == 'ambient'
            and any(m['state'] == 'sent' for m in sent)
        ):
            st['unanswered'] += 1
        requested_policy = dict(policy)
        policy, policy_reasons = apply_policy(self, st, policy)
        st["policy"] = policy
        result = {"messages": sent, "memories": memories, "policy": policy}
        self.store.commit_finish(scope, st, batch_id, result)
        self.trace(scope, 'policy', policy)
        self.trace(
            scope,
            'policy_decision',
            {'requested': requested_policy, 'effective': policy, 'reasons': policy_reasons},
        )

        return result

    def summary_due(self, scope):
        if not self.settings.summary_model or not self.budget(
            scope, "summary_calls", self.settings.summary_per_hour, 3600
        ):
            return False
        state = self.store.state(scope)
        events = self.summary_events(scope)
        return bool(events) and (
            len(events) >= self.settings.summary_count
            or self.clock.now() - events[0].time >= self.settings.summary_age
        )

    def summary_events(self, scope):
        state = self.store.state(scope)
        events = self.store.events(
            scope,
            after=state['summary_cursor'],
            now=self.clock.now(),
            limit=self.settings.summary_count * 5,
        )
        useful = [
            e for e in events if e.speaker_id != scope.bot_id and not e.text.strip().startswith('/')
        ]
        if not useful and events:
            state['summary_cursor'] = events[-1].seq
            self.store.save_state(scope, state)
        return useful[: self.settings.summary_count]

    async def consolidate(self, scope):
        self.task_settings.set(copy.deepcopy(self._settings))
        self.task_revision.set(self.store.revision(scope))
        state = self.store.state(scope)
        events = self.summary_events(scope)
        if not events or not self.budget(
            scope, "summary_calls", self.settings.summary_per_hour, 3600, True
        ):
            return
        messages = [
            {
                "role": "system",
                "content": SYSTEM
                + "\n后台整理：只提取有长期价值的记忆。finish_turn.messages 必须为空。不要发言。",
            }
        ]
        fitted = []
        for event in events:
            message = self.event_message(event)
            if estimate(
                messages + [message], self.settings.image_token_reserve
            ) > self.input_budget(self.settings.summary_model):
                break
            messages.append(message)
            fitted.append(event)
        events = fitted
        if not events:
            self.trace(scope, 'consolidation_skipped', {'reason': 'single_message_exceeds_budget'})
            return
        assets = {
            p['asset_id']
            for m in messages
            for p in (m.get('content') if isinstance(m.get('content'), list) else [])
            if p.get('type') == 'image_ref'
        }
        self.media.pin(assets)
        try:
            self.trace(
                scope,
                'aux_model_request',
                {'task': 'consolidation', 'model': self.settings.summary_model},
            )
            result = await self.gateway.query_llm(
                self.settings.summary_model,
                await self.model_messages(scope, self.settings.summary_model, messages),
                [TOOLS[-1]],
                {"max_tokens": self.settings.output_tokens, "timeout": self.settings.timeout},
            )
            self.assert_revision(scope)
            self.trace(scope, "model_usage", {"task": "consolidation", "usage": result.usage})
            visible = {e.message_id for e in events}
            if (
                len(result.tool_calls) != 1
                or result.tool_calls[0]['function']['name'] != 'finish_turn'
            ):
                raise ValueError('Consolidation did not return one finish_turn')
            for call in result.tool_calls:
                if call["function"]["name"] != "finish_turn":
                    continue
                args = json.loads(call["function"]["arguments"])
                validate(args, FINISH)
                if args['messages']:
                    raise ValueError('Consolidation attempted to speak')
                for proposal in args["memory_proposals"]:
                    try:
                        self.store.propose(scope, proposal, self.clock.now(), visible)
                    except Exception as exc:
                        self.trace(scope, "memory_rejected", {"error": str(exc)})
            state = self.store.state(scope)
            state["summary_cursor"] = events[-1].seq
            self.store.save_state(scope, state)

        except Exception as exc:
            self.trace(
                scope, "consolidation_error", {"type": type(exc).__name__, "error": str(exc)[:500]}
            )
        finally:
            self.media.unpin(assets)

    async def close(self):
        tasks = [*self.tasks.values(), *self.summary_tasks.values(), *self.index_tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot(self):
        return {
            "time": self.clock.now(),
            "busy": sorted(
                {
                    k
                    for workers in (self.tasks, self.summary_tasks)
                    for k, t in workers.items()
                    if not t.done()
                }
            ),
            "background_busy": [k for k, t in self.summary_tasks.items() if not t.done()],
            "scopes": {
                s.key: {
                    "state": self.store.state(s),
                    "memories": self.store.memories(s, statuses=None, now=self.clock.now()),
                }
                for s in self.store.scopes()
            },
            "actions": [dict(r) for r in self.store.db.execute("SELECT * FROM actions")],
            "media_bytes": self.media.usage(),
        }

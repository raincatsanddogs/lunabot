"""临时会话状态：上下文裁剪、对话关系和有时限的关注策略。"""

from __future__ import annotations

import copy

import re

from .store import dump

CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]')


def estimate(messages, image_reserve=4096):
    text = dump(messages)
    cjk_count = len(CJK_PATTERN.findall(text))
    other_count = len(text) - cjk_count
    # CJK characters are ~1.15 tokens/char; ASCII/JSON formatting are ~0.32 tokens/char
    total = int(cjk_count * 1.15 + other_count * 0.32)
    for message in messages:
        if isinstance(message.get('content'), list):
            total += sum(image_reserve for p in message['content'] if p.get('type') == 'image_ref')
    return total


def completed_tail(messages, token_limit):
    """Keep entire assistant/tool exchanges together for the summarizer."""
    groups, group = [], []
    for message in messages:
        if message['role'] != 'tool' and group and group[-1]['role'] != 'assistant':
            groups.append(group)
            group = []
        group.append(message)
    if group:
        groups.append(group)
    kept = []
    for group in reversed(groups):
        if estimate(group + kept) > token_limit:
            break
        kept = group + kept
    return kept


def fit_event(message, budget, image_reserve):
    """An oversized platform message stays queryable; omissions are explicit."""
    result = copy.deepcopy(message)
    if estimate([result], image_reserve) <= budget:
        return result
    content = []
    for part in result['content']:
        if part['type'] == 'text':
            part['text'] = part['text'][: max(80, budget // 4)]
        content.append(part)
        if estimate([{'role': 'user', 'content': content}], image_reserve) > budget - 220:
            content.pop()
            break
    content.append(
        {
            'type': 'text',
            'text': '[输入预算不足，后续内容未展示；可用 read_messages / load_media 查询原消息。]',
        }
    )
    return {'role': 'user', 'content': content}


# 只由真实互动建立关注状态，避免 bot 自己的发言无限延长关注。
def addressed_elsewhere(engine, event):
    if engine.direct(event):
        return False
    for segment in event.segments:
        if segment['type'] == 'at' and str(segment['data'].get('qq')) != event.bot_id:
            return True
        if segment['type'] == 'reply':
            rows = engine.store.events(
                event.scope, ids=[str(segment['data'].get('id'))], now=engine.clock.now()
            )
            if not rows or rows[0].speaker_id != event.bot_id:
                return True
    return False


def observe(engine, scope, events):
    state, now = engine.store.state(scope), engine.clock.now()
    state['contacts'] = {
        uid: info for uid, info in state['contacts'].items() if info['expires_at'] > now
    }
    for event in events:
        if event.seq <= state['contact_cursor'] or event.speaker_id == scope.bot_id:
            continue
        waiting = state.get('awaiting', {})
        pending_answer = (
            waiting.get('expires_at', 0) > now
            and event.speaker_id in waiting.get('user_ids', [])
            and not addressed_elsewhere(engine, event)
        )
        if engine.direct(event) or pending_answer:
            state['contacts'][event.speaker_id] = {
                'message_id': event.message_id,
                'expires_at': now + engine.settings.attention_seconds,
            }
            state['unanswered'] = 0
            if pending_answer:
                state['awaiting'] = {}
        state['contact_cursor'] = max(state['contact_cursor'], event.seq)
    engine.store.save_state(scope, state)
    return state


def eligible(state, now):
    users = {uid for uid, info in state.get('contacts', {}).items() if info['expires_at'] > now}
    waiting = state.get('awaiting', {})
    if waiting.get('expires_at', 0) > now:
        users.update(waiting.get('user_ids', []))
    return users


def apply_policy(engine, state, requested):
    policy, reasons = dict(requested), []
    policy['focus_user_ids'] = list(
        dict.fromkeys(
            uid for uid in requested['focus_user_ids'] if uid in eligible(state, engine.clock.now())
        )
    )
    if policy['focus_user_ids'] != requested['focus_user_ids']:
        reasons.append('focus_without_current_human_interaction')
    if state['unanswered'] >= engine.settings.unanswered_limit:
        policy = {
            'ambient_p': engine.settings.ambient_p,
            'followup_p': engine.settings.followup_p,
            'focus_user_ids': [],
            'ttl_seconds': engine.settings.policy_ttl,
        }
        state['awaiting'] = {}
        reasons.append('unanswered_ambient_turns')
    policy['expires_at'] = engine.clock.now() + policy['ttl_seconds']
    return policy, reasons

"""Ordered tool loop with a durable cursor at each model response."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time

from jsonschema import validate

from .context import estimate, observe
from .store import dump
from .tools import TOOLS
from .websearch import public_url


def save(engine, scope, batch_id, journal, state=None):
    with engine.store.db:
        pairs = [('turn:' + batch_id, journal)]
        if state is not None:
            pairs.append(('state:' + scope.key, state))
        for key, value in pairs:
            engine.store.db.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dump(value)))


def candidates(engine, scope, query, limit):
    records = engine.stickers.search(scope, query, limit)
    info, attachments = [], []
    for row in records:
        item = {k: row.get(k, '') for k in ('id', 'description', 'text', 'intents', 'tone', 'avoid_contexts', 'persona_tags')}
        item['sticker_id'] = item.pop('id')
        info.append(item)
        attachments.extend([
            {'type': 'text', 'text': '[回复素材，不是群消息或用户事实] ' + dump(item)},
            {'type': 'image_ref', 'asset_id': row['asset_id'], 'preview': True,
             'sticker_description': row.get('description', '') + '；图中文字：' + row.get('text', '')},
        ])
    engine.media.pin(r['asset_id'] for r in records)
    return {'candidates': info, '_attachments': attachments, '_sticker_ids': [r['id'] for r in records]}


def attach(engine, scope, state, parts):
    fitted = []
    budget = engine.input_budget(state['model'])
    for part in parts:
        proposed = state['context'] + [{'role': 'user', 'content': fitted + [part]}]
        if estimate(proposed, engine.settings.image_token_reserve) <= budget * .95:
            fitted.append(part)
        elif part.get('type') == 'image_ref':
            fitted.append({'type': 'text', 'text': '[该预览因输入预算不足省略；仅可依据素材描述]'})
    if fitted:
        state['context'].append({'role': 'user', 'content': fitted})


async def web_call(engine, scope, batch_id, call_key, name, args, visible):
    provider = engine.settings.search_provider
    if not provider or not hasattr(engine.platform, 'search'):
        return {'error': 'websearch_unavailable'}
    cache_key = 'web-call:' + call_key
    old = engine.store.get(cache_key)
    if old is not None:
        return old
    if name == 'read_web':
        urls = set(engine.store.state(scope).get('web_urls', []))
        for event in engine.store.events(scope, ids=list(visible), now=engine.clock.now(), limit=100000):
            urls.update(url.rstrip('.,;!?，。！？；') for url in re.findall(r'https?://[^\s<>"\[\]()]+', event.text))
        if args['url'] not in urls or not public_url(args['url']):
            return {'error': 'URL was not visible or is not public'}
    budget_key = 'web-budget:' + batch_id
    budget = engine.store.get(budget_key, {})
    limit = engine.settings.max_search_calls if name == 'web_search' else engine.settings.max_web_read_calls
    if budget.get(name, 0) >= limit:
        return {'error': 'web_budget_exhausted'}
    budget[name] = budget.get(name, 0) + 1
    engine.store.set(budget_key, budget)
    engine.store.set(cache_key, {'error': 'search_interrupted; not automatically repeated'})
    if name == 'web_search':
        args = {**args, 'limit': min(args.get('limit', 5), engine.settings.search_max_results)}
    try:
        result = await engine.platform.search(provider, name, args)
    except Exception:
        result = {'error': 'search_transport_error'}
    if name == 'read_web' and 'content' in result:
        text = result['content']
        result = {**result, 'content': text[:engine.settings.page_max_chars],
                  'truncated': result.get('truncated', False) or len(text) > engine.settings.page_max_chars}
    engine.store.set(cache_key, result)
    return result


def record_result(engine, scope, batch_id, journal, call, value):
    value = copy.deepcopy(value)
    if call['function']['name'] == 'send_message':
        used = engine.store.get('send-budget:' + batch_id, {}).get('attempts', 0)
        value['remaining_messages'] = max(0, engine.settings.max_messages - used)
    active = journal['active']
    parts = value.pop('_attachments', [])
    if value.get('attachment'):
        parts.append(value.pop('attachment'))
        if value.get('image'):
            parts.append(value.pop('image'))
    active.setdefault('attachments', []).extend(parts)
    state = engine.store.state(scope)
    state['visible_stickers'] = sorted(set(state.get('visible_stickers', [])) | set(value.pop('_sticker_ids', [])))
    if call['function']['name'] == 'web_search':
        state['web_urls'] = list(dict.fromkeys(state.get('web_urls', []) + [r['url'] for r in value.get('results', [])]))[-200:]
    content = dump(value)
    if len(content) > 16000:
        content = dump({'truncated': True, 'excerpt': content[:15000]})
    message = {'role': 'tool', 'tool_call_id': call['id'], 'name': call['function']['name'], 'content': content}
    # Bound results *before* inserting them, so compaction never discards an
    # unread web page immediately before the model's continuation.
    room = max(512, engine.input_budget(state['model']) - estimate(state['context'], engine.settings.image_token_reserve) - 512)
    if len(dump(message).encode('utf-8')) > room:
        value['truncated'] = True
        if isinstance(value.get('content'), str):
            while len(dump({**message, 'content': dump(value)}).encode('utf-8')) > room and value['content']:
                value['content'] = value['content'][:len(value['content']) * 3 // 4]
        elif isinstance(value.get('results'), list):
            while len(dump({**message, 'content': dump(value)}).encode('utf-8')) > room and value['results']:
                value['results'].pop()
        else:
            excerpt = dump(value)
            value = {'truncated': True, 'excerpt': excerpt[:max(30, room // 8)]}
        message['content'] = dump(value)
    state['context'].append(message)
    state['visible_sources'] = journal['visible']
    active['cursor'] += 1
    save(engine, scope, batch_id, journal, state)
    engine.trace(scope, 'tool_result', {'call_id': call['id'], 'tool': call['function']['name'], 'result': value})


async def run_turn(engine, scope, events, batch_id):
    engine.task_settings.set(copy.deepcopy(engine._settings))
    engine.task_revision.set(engine.store.revision(scope))
    started = time.monotonic()
    batch = engine.store.db.execute('SELECT value FROM batches WHERE id=?', (batch_id,)).fetchone()
    generation = json.loads(batch[0]).get('generation', 0) if batch else 0
    journal = engine.store.get('turn:' + batch_id, {
        'next_round': 0, 'visible': [], 'watermark': max(e.seq for e in events),
        'consumed': [e.message_id for e in events], 'rebased': False, 'corrections': 0,
    })
    authors = {e.speaker_id for e in events}
    interrupted = False
    try:
        if not journal.get('active'):
            await engine.prepare_context(scope, required_ids=journal['consumed'])
            journal['visible'] = engine.store.state(scope)['visible_sources']
        committed = engine.store.get('commit:' + batch_id)
        if committed and not journal.get('active'):
            await engine.finish(scope, batch_id, committed['args'], set(committed['visible']), journal['watermark'], authors)
            engine.store.batch_done(batch_id, 'finished')
            return
        tools = TOOLS
        available = False
        if engine.settings.search_provider and hasattr(engine.platform, 'describe_search'):
            try:
                available = (await engine.platform.describe_search(engine.settings.search_provider)).get('available', False)
            except Exception:
                pass
        if not available:
            tools = [t for t in TOOLS if t['function']['name'] not in ('web_search', 'read_web')]
        if not journal.get('prefetched') and not journal.get('active'):
            value = candidates(engine, scope, ' '.join(e.text for e in events), engine.settings.sticker_prefetch)
            st = engine.store.state(scope)
            attach(engine, scope, st, value['_attachments'])
            st['visible_stickers'] = sorted(set(st.get('visible_stickers', [])) | set(value['_sticker_ids']))
            journal['prefetched'] = True
            save(engine, scope, batch_id, journal, st)
            engine.media.unpin(p['asset_id'] for p in value['_attachments'] if p.get('type') == 'image_ref')
        while journal.get('active') or journal['next_round'] < engine.settings.max_rounds:
            if not journal.get('active'):
                if not engine.enabled.get(scope.key, True):
                    break
                updates = engine.relevant_updates(scope, journal['watermark'], authors)
                if updates:
                    if all(e.message_id in journal['visible'] for e in updates):
                        engine.trace(scope, 'updates_in_context', {'batch_id': batch_id, 'new_messages': [e.message_id for e in updates]})
                    observe(engine, scope, updates)
                    journal['consumed'] = list(dict.fromkeys(journal['consumed'] + [e.message_id for e in updates]))
                    journal['watermark'] = max(e.seq for e in updates)
                    await engine.prepare_context(scope, required_ids=journal['consumed'])
                state = engine.store.state(scope)
                journal['visible'] = state['visible_sources']
                model, context = state['model'], state['context']
                if estimate(context, engine.settings.image_token_reserve) > engine.input_budget(model):
                    await engine.prepare_context(scope, force=True, required_ids=journal['consumed'])
                    state = engine.store.state(scope)
                    context = state['context']
                    journal['visible'] = state['visible_sources']
                    if estimate(context, engine.settings.image_token_reserve) > engine.input_budget(model):
                        raise ValueError('Context exceeds configured input budget')
                if not engine.budget(scope, 'calls', engine.settings.calls_per_minute, 60, True):
                    engine.trace(scope, 'budget_exhausted', {'batch_id': batch_id})
                    break
                index = journal['next_round']
                journal['next_round'] += 1
                save(engine, scope, batch_id, journal)
                request = await engine.model_messages(scope, model, context)
                engine.trace(scope, 'model_request', {'batch_id': batch_id, 'round': index, 'model': model,
                             'prefix_hash': hashlib.sha256(dump(context).encode()).hexdigest(),
                             'input_estimate': estimate(context, engine.settings.image_token_reserve)})
                try:
                    result = await engine.gateway.query_llm(model, request, tools,
                        {'max_tokens': engine.settings.output_tokens, 'timeout': engine.settings.timeout})
                    engine.assert_revision(scope)
                except Exception as exc:
                    engine.trace(scope, 'model_error', {'model': model, 'type': type(exc).__name__})
                    chain = [engine.settings.model, *engine.settings.fallback_models]
                    position = chain.index(model) if model in chain else len(chain)
                    if position + 1 >= len(chain):
                        raise
                    await engine.prepare_context(scope, force=True, model=chain[position + 1], required_ids=journal['consumed'])
                    continue
                engine.trace(scope, 'model_usage', {'task': 'chat', 'usage': result.usage, 'finish_reason': result.finish_reason})
                engine.trace(scope, 'model_response', {'batch_id': batch_id, 'round': index, 'model': model, 'assistant_message': result.assistant_message})
                st = engine.store.state(scope)
                if estimate(st['context'] + [result.assistant_message], engine.settings.image_token_reserve) > engine.input_budget(model) * .85:
                    # The model has now consumed the preceding completed exchange.
                    # Compact it before opening a new assistant/tool exchange.
                    await engine.prepare_context(scope, force=True, required_ids=journal['consumed'])
                    st = engine.store.state(scope)
                st['context'].append(result.assistant_message)
                journal['active'] = {'calls': result.tool_calls, 'cursor': 0, 'round': index,
                                     'visible': list(journal['visible']), 'sticker_ids': st.get('visible_stickers', [])}
                save(engine, scope, batch_id, journal, st)
            active = journal['active']
            calls = active['calls']
            if not calls:
                if journal['corrections']:
                    break
                journal['corrections'] += 1
                engine.append_context(scope, [{'role': 'user', 'content': '请用 send_message 发言，随后 finish_turn 结束；普通文本不会发送。'}])
            finishes = [i for i, c in enumerate(calls) if c['function']['name'] == 'finish_turn']
            invalid_finish = len(finishes) > 1 or (finishes and finishes[0] != len(calls) - 1)
            while active['cursor'] < len(calls):
                cursor = active['cursor']
                call = calls[cursor]
                name = call['function']['name']
                visible = set(journal['visible'])
                value = None
                # Consecutive reads run together; writes, sends and finish are barriers.
                reads = {'read_messages', 'get_user_memory', 'search_memory', 'load_media'}
                if name in reads:
                    group = []
                    for candidate in calls[cursor:]:
                        if candidate['function']['name'] not in reads:
                            break
                        group.append(candidate)
                    available_reads = max(0, engine.settings.max_read_calls - active.get('reads', 0))
                    results = await asyncio.gather(*(engine.read_tool(scope, c, visible) for c in group[:available_reads]))
                    results += [{'error': 'read_limit_exceeded'}] * (len(group) - len(results))
                    active['reads'] = active.get('reads', 0) + min(len(group), available_reads)
                    journal['visible'] = sorted(visible)
                    for c, v in zip(group, results):
                        record_result(engine, scope, batch_id, journal, c, v)
                    continue
                try:
                    schema = next(t['function']['parameters'] for t in tools if t['function']['name'] == name)
                    args = json.loads(call['function']['arguments'])
                    validate(args, schema)
                    action_id = f'{batch_id}:tool:{generation}:{active["round"]}:{cursor}'
                    old_action = engine.store.action(action_id) if name == 'send_message' else None
                    already_executed = old_action and old_action['state'] != 'pending'
                    if name == 'finish_turn':
                        already_executed = engine.store.get('result:' + batch_id) is not None
                    if name in ('send_message', 'finish_turn'):
                        if not already_executed and (engine.relevant_updates(scope, journal['watermark'], authors) or not engine.enabled.get(scope.key, True)):
                            active['stale'] = True
                            raise ValueError('draft superseded by relevant new messages or group disabled')
                    if name == 'send_message':
                        if journal.get('completed'):
                            raise ValueError('Memory review only; sending already finished')
                        for part in args['segments']:
                            if part['type'] == 'sticker' and part['sticker_id'] not in active.get('sticker_ids', []):
                                raise ValueError('Sticker was not visible to this model response')
                        value = await engine.send_message(scope, batch_id, action_id,
                                                          args, set(active['visible']), journal['watermark'], authors)
                        if value.get('result', {}).get('reason') == 'disabled_or_new_messages':
                            active['stale'] = True
                    elif name in ('web_search', 'read_web'):
                        value = await web_call(engine, scope, batch_id, f'{batch_id}:{generation}:{active["round"]}:{cursor}', name, args, set(active['visible']))
                    elif name == 'search_stickers':
                        if active.get('reads', 0) >= engine.settings.max_read_calls:
                            raise ValueError('read_limit_exceeded')
                        active['reads'] = active.get('reads', 0) + 1
                        value = candidates(engine, scope, args['query'], args.get('limit', 4))
                    elif name == 'clarify_memories':
                        value = await engine.read_tool(scope, call, visible)
                    elif name == 'finish_turn':
                        if invalid_finish:
                            raise ValueError('Exactly one finish_turn is allowed and must be last')
                        if journal.get('completed'):
                            value = {'messages': [], 'memories': await engine.propose_memories(scope, args['memory_proposals'], visible)}
                            active['finished'] = True
                        else:
                            value = await engine.finish(scope, batch_id, args, set(active['visible']), journal['watermark'], authors)
                            journal['completed'] = value
                            if any(m.get('possible_duplicates') for m in value['memories']) and journal['next_round'] < engine.settings.max_rounds:
                                active['review'] = True
                            else:
                                active['finished'] = True
                    else:
                        value = {'error': 'unknown_tool'}
                except Exception as exc:
                    value = {'error': str(exc)[:500]}
                journal['visible'] = sorted(visible)
                record_result(engine, scope, batch_id, journal, call, value)
            state = engine.store.state(scope)
            attach(engine, scope, state, active.get('attachments', []))
            engine.media.unpin(p['asset_id'] for p in active.get('attachments', []) if p.get('type') == 'image_ref')
            if active.get('review'):
                state['context'].append({'role': 'user', 'content': '发送和策略已完成。只澄清 possible_duplicates，不能再发送；finish_turn.messages 必须为空。'})
            finished, stale = active.get('finished'), active.get('stale')
            journal.pop('active')
            save(engine, scope, batch_id, journal, state)
            if finished:
                engine.store.batch_done(batch_id, 'finished')
                return
            if stale:
                engine.trace(scope, 'draft_cancelled', {'batch_id': batch_id})
                if journal['rebased'] or not engine.enabled.get(scope.key, True):
                    break
                journal['rebased'] = True
                save(engine, scope, batch_id, journal)
        engine.store.batch_done(batch_id, 'finished' if journal.get('completed') else 'stopped')
    except asyncio.CancelledError:
        interrupted = True
        engine.trace(scope, 'turn_interrupted', {'batch_id': batch_id})
        raise
    except Exception as exc:
        engine.trace(scope, 'turn_error', {'batch_id': batch_id, 'type': type(exc).__name__, 'error': str(exc)[:500]})
        engine.store.batch_done(batch_id, 'finished' if journal.get('completed') else 'failed')
    finally:
        if not interrupted:
            if journal.get('active'):
                active = journal['active']
                engine.media.unpin(p['asset_id'] for p in active.get('attachments', []) if p.get('type') == 'image_ref')
                for call in active['calls'][active['cursor']:]:
                    record_result(engine, scope, batch_id, journal, call, {'error': 'turn_stopped; consult actual action records'})
                journal.pop('active')
                save(engine, scope, batch_id, journal)
            engine.store.mark_handled(engine.store.events(scope, ids=journal['consumed'], limit=100000))
            engine.trace(scope, 'turn_complete', {'batch_id': batch_id,
                'sent_count': sum(r['state'] == 'sent' for r in engine.send_results(scope, batch_id)),
                'latency_wall_seconds': time.monotonic() - started})

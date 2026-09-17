"""Durable sends shared by send_message and the legacy finish_turn envelope."""
from __future__ import annotations

import base64
import asyncio
import json

from jsonschema import validate

from .store import dump
from .tools import SEND
from .types import Event


class SendActions:
    def send_results(self, scope, batch_id):
        return [
            {'action_id': row['id'], 'state': row['state'], 'result': json.loads(row['result']) if row['result'] else {}}
            for row in self.store.db.execute(
                'SELECT * FROM actions WHERE scope=? AND id LIKE ? ORDER BY rowid', (scope.key, batch_id + ':%')
            )
        ]

    def prepare_send(self, scope, message, visible):
        if 'segments' not in message:
            message = {**message, 'segments': [{'type': 'text', 'text': message.get('text', '')}]}
            message.pop('text', None)
        validate(message, SEND)
        events = self.store.events(scope, ids=list(visible), now=self.clock.now(), limit=100000)
        users = {e.speaker_id for e in events}
        if not set(message.get('at_user_ids', []) + message.get('awaiting_user_ids', [])).issubset(users):
            raise ValueError('Unknown reply target')
        if message.get('reply_to_message_id') and message['reply_to_message_id'] not in visible:
            raise ValueError('Unknown quoted message')
        text = ''.join(p['text'] for p in message['segments'] if p['type'] == 'text')
        if len(text) > self.settings.reply_max_length:
            raise ValueError('Invalid reply length')
        segments = []
        if message.get('reply_to_message_id'):
            segments.append({'type': 'reply', 'data': {'id': message['reply_to_message_id']}})
        segments.extend({'type': 'at', 'data': {'qq': uid}} for uid in message.get('at_user_ids', []))
        shown = set(self.store.state(scope).get('visible_stickers', []))
        for part in message['segments']:
            if part['type'] == 'text':
                if part['text'].strip():
                    segments.append({'type': 'text', 'data': {'text': part['text']}})
            else:
                sid = part['sticker_id']
                if sid not in shown:
                    raise ValueError('Sticker was not presented in this conversation')
                sticker = self.stickers.record(scope, sid, active=True)
                segments.append({'type': 'image', 'data': {
                    'asset_id': sticker['asset_id'], 'sticker_id': sid,
                    'description': sticker.get('description', ''), 'visible_text': sticker.get('text', ''),
                }})
        if not any(s['type'] in ('text', 'image') for s in segments):
            raise ValueError('Invalid reply length')
        return message, segments

    def note_sent(self, scope, action_id):
        action = self.store.action(action_id)
        if not action or action['state'] != 'sent' or self.store.get('noted:' + action_id):
            return
        payload, response = action['payload'], action['result'] or {}
        if not response.get('message_id'):
            return
        now = response.get('sent_at', self.clock.now())
        event = Event(scope.bot_id, scope.group_id, str(response['message_id']), scope.bot_id, now, payload['segments'], 'bot')
        st = self.store.state(scope)
        message = payload.get('message', {})
        text = ''.join(s['data'].get('text', '') for s in payload['segments'] if s['type'] == 'text')
        waiting = message.get('awaiting_user_ids', [])
        if waiting and any(mark in text for mark in ('?', '？', '吗', '呢')) and not st.get('awaiting') and st['unanswered'] == 0:
            st['awaiting'] = {'message_id': event.message_id, 'user_ids': waiting,
                             'expires_at': now + min(payload.get('ttl', self.settings.policy_ttl), self.settings.attention_seconds)}
        batch_id = payload.get('batch_id', action_id.split(':', 1)[0])
        batch = self.store.db.execute('SELECT value FROM batches WHERE id=?', (batch_id,)).fetchone()
        count_key = 'spoken:' + batch_id
        if batch and json.loads(batch[0]).get('reason') == 'ambient' and not self.store.get(count_key):
            st['unanswered'] += 1
        with self.store.db:
            self.store.db.execute(
                'INSERT OR IGNORE INTO events(scope,message_id,speaker,time,payload,handled,received) VALUES (?,?,?,?,?,1,?)',
                (scope.key, event.message_id, scope.bot_id, now, dump(event.to_dict()), now),
            )
            for key, value in [('state:' + scope.key, st), ('noted:' + action_id, True), (count_key, True)]:
                self.store.db.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dump(value)))

    async def send_message(self, scope, batch_id, action_id, message, visible, watermark=0, authors=(), ttl=None):
        self.assert_revision(scope)
        action = self.store.action(action_id)
        if action and action['state'] != 'pending':
            self.note_sent(scope, action_id)
            return {'action_id': action_id, 'state': action['state'], 'result': action['result'] or {}}
        if not action:
            message, segments = self.prepare_send(scope, message, visible)
            action = self.store.start_action(action_id, scope, {
                'batch_id': batch_id, 'message': message, 'segments': segments,
                'ttl': ttl or self.settings.policy_ttl,
            })
        def cancelled(reason):
            result = {'reason': reason}
            self.store.finish_action(action_id, 'cancelled', result)
            return {'action_id': action_id, 'state': 'cancelled', 'result': result}
        def stale():
            return not self.enabled.get(scope.key, True) or (watermark and self.relevant_updates(scope, watermark, authors))
        if stale():
            return cancelled('disabled_or_new_messages')
        last = self.store.get('last-send:' + scope.key)
        if last is not None:
            await self.clock.sleep(max(0, last + self.settings.send_interval_seconds - self.clock.now()))
        self.assert_revision(scope)
        if stale():
            return cancelled('disabled_or_new_messages')
        budget = self.store.get('send-budget:' + batch_id, {'attempts': 0, 'stickers': 0})
        # Old persisted commits already used actions; account for them on upgrade.
        if not self.store.get('send-budget:' + batch_id):
            budget['attempts'] = sum(r['state'] in ('sent', 'unknown', 'failed', 'sending') for r in self.send_results(scope, batch_id))
        if budget['attempts'] >= self.settings.max_messages:
            return cancelled('message_budget_exhausted')
        images = [s['data'] for s in action['payload']['segments'] if s['type'] == 'image']
        if budget['stickers'] + len(images) > self.settings.max_stickers:
            return cancelled('sticker_budget_exhausted')
        if len({p['sticker_id'] for p in images}) != len(images):
            return cancelled('duplicate_sticker')
        wire = []
        for segment in action['payload']['segments']:
            if segment['type'] != 'image':
                wire.append(segment)
                continue
            data = segment['data']
            try:
                sticker = self.stickers.record(scope, data['sticker_id'], active=True)
                if sticker['last_sent'] is not None and self.clock.now() - sticker['last_sent'] < self.settings.sticker_cooldown:
                    return cancelled('sticker_cooldown')
                binary = self.media.path(sticker['asset_id']).read_bytes()
                if len(binary) > self.settings.media_file_bytes:
                    return cancelled('image_too_large')
                wire.append({'type': 'image', 'data': {**data, 'file': 'base64://' + base64.b64encode(binary).decode()}})
            except (ValueError, OSError):
                return cancelled('sticker_unavailable')
        budget['attempts'] += 1
        budget['stickers'] += len(images)
        with self.store.db:
            self.store.db.execute("UPDATE actions SET state='sending' WHERE id=?", (action_id,))
            for key, value in [('send-budget:' + batch_id, budget), ('last-send:' + scope.key, self.clock.now())]:
                self.store.db.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dump(value)))
            for data in images:
                self.store.db.execute('UPDATE stickers SET last_sent=? WHERE id=?', (self.clock.now(), data['sticker_id']))
        try:
            response = await self.platform.send(scope, wire, action_id)
            state = response.get('state', 'sent' if response.get('message_id') else 'unknown')
        except asyncio.CancelledError:
            self.store.finish_action(action_id, 'unknown', {'reason': 'interrupted_during_send'})
            raise
        except Exception as exc:
            state, response = 'unknown', {'error': type(exc).__name__}
        if state not in ('sent', 'failed', 'unknown', 'cancelled'):
            state = 'unknown'
        response = {**response, 'sent_at': self.clock.now()}
        self.store.finish_action(action_id, state, response)
        self.note_sent(scope, action_id)
        result = {'action_id': action_id, 'state': state, 'result': response}
        self.trace(scope, 'send', result)
        self.assert_revision(scope)
        return result

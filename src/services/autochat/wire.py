"""Validate transient image bytes; keep only media references in durable actions."""
import base64
import copy
import hashlib
import re


def outgoing_segments(segments, file_limit=20 * 1024 * 1024):
    if not isinstance(segments, list) or not 1 <= len(segments) <= 40:
        raise ValueError('Unsupported message segments')
    wire, stored = [], []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('data'), dict):
            raise ValueError('Invalid segment')
        kind, data = segment.get('type'), segment['data']
        if kind == 'image':
            source = data.get('file', '')
            if not isinstance(source, str) or not source.startswith('base64://') or len(source) > file_limit * 4 // 3 + 20:
                raise ValueError('Only bounded inline image bytes are accepted')
            binary = base64.b64decode(source[9:], validate=True)
            if len(binary) > file_limit or hashlib.sha256(binary).hexdigest() != data.get('asset_id'):
                raise ValueError('Image asset hash mismatch')
            if not re.fullmatch(r'[a-f0-9]{24}', data.get('sticker_id', '')):
                raise ValueError('Invalid sticker ID')
            compact = {key: data[key] for key in ('asset_id', 'sticker_id')}
            compact.update({key: str(data.get(key, ''))[:2000] for key in ('description', 'visible_text')})
            stored.append({'type': 'image', 'data': compact})
            wire.append({'type': 'image', 'data': {'file': source}})
        elif kind in ('text', 'at', 'reply'):
            key = {'text': 'text', 'at': 'qq', 'reply': 'id'}[kind]
            if not isinstance(data.get(key), str) or len(data[key]) > (12000 if kind == 'text' else 128):
                raise ValueError('Invalid message segment data')
            item = {'type': kind, 'data': {key: data[key]}}
            wire.append(item)
            stored.append(copy.deepcopy(item))
        else:
            raise ValueError('Unsupported message segments')
    return wire, stored

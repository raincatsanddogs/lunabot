"""读取、校验 autochat 配置；不依赖 NoneBot，也不读取模型密钥。"""

from dataclasses import asdict
from pathlib import Path
import math

import yaml

from .types import Settings


FIELDS = {
    # 外部 YAML 使用按用途分节的名称；运行时 Settings 保持直接属性访问。
    'chat.trigger.ambient_p': 'ambient_p',
    'chat.trigger.followup_p': 'followup_p',
    'chat.trigger.ttl_seconds': 'policy_ttl',
    'chat.trigger.attention_seconds': 'attention_seconds',
    'chat.trigger.unanswered_limit': 'unanswered_limit',
    'chat.trigger.debounce': 'debounce',
    'chat.trigger.max_batch_wait': 'max_batch_wait',
    'chat.trigger.seed': 'seed',
    'chat.budget.wakes_per_minute': 'wakes_per_minute',
    'chat.budget.calls_per_minute': 'calls_per_minute',
    'chat.budget.max_rounds': 'max_rounds',
    'chat.budget.max_read_calls': 'max_read_calls',
    'chat.reply_max_length': 'reply_max_length',
    'chat.max_messages': 'max_messages',
    'chat.stickers.annotation_model': 'sticker_annotation_model',
    'chat.stickers.prefetch': 'sticker_prefetch',
    'chat.stickers.max_per_turn': 'max_stickers',
    'chat.stickers.cooldown_seconds': 'sticker_cooldown',
    'chat.context.input_tokens': 'input_tokens',
    'chat.context.image_token_reserve': 'image_token_reserve',
    'chat.llm.max_tokens': 'output_tokens',
    'chat.llm.timeout': 'timeout',
    'chat.llm.emb_model': 'embedding_model',
    'chat.llm.vision_model': 'vision_model',
    'summary.model': 'summary_model',
    'summary.count': 'summary_count',
    'summary.age_seconds': 'summary_age',
    'summary.per_hour': 'summary_per_hour',
    'chat.media.total_bytes': 'media_bytes',
    'chat.media.file_bytes': 'media_file_bytes',
    'chat.media.original_days': 'original_days',
    'chat.media.preview_days': 'preview_days',
}
RPC_FIELDS = {'host', 'port', 'token', 'consumer_id', 'url', 'max_message_bytes'}


def parse_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('autochat: expected YAML mapping')
    if not isinstance(raw.get('rpc', {}), dict):
        raise ValueError('rpc: expected mapping')
    values, rpc = {}, dict(raw.get('rpc') or {})
    allowed = set(FIELDS) | {'log_level', 'chat.llm.model', 'chat.prompt.persona'}

    def visit(value, path=''):
        if path in allowed:
            values[path] = value
        elif isinstance(value, dict):
            if path and not any(k.startswith(path + '.') for k in allowed):
                raise ValueError(f'{path}: unknown configuration section')
            for key, child in value.items():
                name = str(key) if not path else path + '.' + str(key)
                if name == 'rpc':
                    continue
                visit(child, name)
        else:
            raise ValueError(f'{path}: unknown configuration field')

    visit(raw)
    for name in rpc:
        if name not in RPC_FIELDS:
            raise ValueError(f'rpc.{name}: unknown configuration field')
    rpc = {
        'host': '127.0.0.1',
        'port': 12345,
        'token': '',
        'consumer_id': 'autochat',
        'max_message_bytes': 128 * 1024 * 1024,
        **rpc,
    }
    if type(rpc['port']) is not int or not 1 <= rpc['port'] <= 65535:
        raise ValueError('rpc.port: expected port between 1 and 65535')
    if type(rpc['max_message_bytes']) is not int or rpc['max_message_bytes'] <= 0:
        raise ValueError('rpc.max_message_bytes: expected positive integer')
    for key in ('host', 'token', 'consumer_id'):
        if not isinstance(rpc[key], str) or (key != 'token' and not rpc[key]):
            raise ValueError(f'rpc.{key}: expected string')
    rpc.setdefault(
        'url',
        'ws://'
        + ('127.0.0.1' if rpc['host'] == '0.0.0.0' else rpc['host'])
        + ':'
        + str(rpc['port']),
    )
    if not isinstance(rpc['url'], str) or not rpc['url'].startswith(('ws://', 'wss://')):
        raise ValueError('rpc.url: expected WebSocket URL')
    models = values.get('chat.llm.model')
    if (
        not isinstance(models, list)
        or not models
        or any(not isinstance(m, str) or not m for m in models)
    ):
        raise ValueError('chat.llm.model: expected nonempty list of model aliases')
    if len(set(models)) != len(models):
        raise ValueError('chat.llm.model: duplicate alias')
    personas = values.get('chat.prompt.persona', {'default': Settings().persona})
    if not isinstance(personas, dict) or any(not isinstance(v, str) for v in personas.values()):
        raise ValueError('chat.prompt.persona: expected default/group mapping of strings')
    if len({str(k) for k in personas}) != len(personas):
        raise ValueError('chat.prompt.persona: duplicate normalized group ID')
    defaults = asdict(Settings())
    settings = {}
    for key, field in FIELDS.items():
        if key not in values:
            continue
        value, default = values[key], defaults[field]
        if isinstance(default, str):
            valid = isinstance(value, str)
        elif field in ('ambient_p', 'followup_p', 'debounce', 'max_batch_wait', 'timeout', 'sticker_cooldown'):
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        elif isinstance(default, int):
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        if not valid:
            raise ValueError(f'{key}: invalid value type')
        settings[field] = value
    for key in ('timeout', 'original_days', 'preview_days'):
        if settings.get(key, defaults[key]) <= 0:
            raise ValueError(
                next(path for path, field in FIELDS.items() if field == key) + ': must be positive'
            )
    for field, valid in (
        ('max_messages', settings.get('max_messages', defaults['max_messages']) <= 2),
        ('input_tokens', settings.get('input_tokens', defaults['input_tokens']) >= 2048),
        ('debounce', settings.get('debounce', defaults['debounce']) >= 0),
        (
            'max_batch_wait',
            settings.get('max_batch_wait', defaults['max_batch_wait'])
            >= settings.get('debounce', defaults['debounce']),
        ),
    ):
        if not valid:
            raise ValueError(
                next(path for path, key in FIELDS.items() if key == field) + ': invalid limit'
            )
    try:
        result = Settings(
            **settings,
            model=models[0],
            fallback_models=models[1:],
            persona=personas.get('default', defaults['persona']),
            personas={str(k): v for k, v in personas.items() if str(k) != 'default'},
        )
    except (ValueError, TypeError) as exc:
        message = str(exc)
        for path, field in FIELDS.items():
            if field in message:
                message = message.replace(field, path)
        raise ValueError(message) from exc
    level = values.get('log_level', 'INFO')
    if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        raise ValueError('log_level: invalid level')
    return result, rpc, level


def config_from_settings(settings, rpc):
    """Used by external test drivers to emit the same public configuration."""
    values = asdict(Settings(**settings))
    result = {'log_level': 'INFO', 'rpc': rpc}

    def put(path, value):
        target = result
        parts = path.split('.')
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    for path, field in FIELDS.items():
        put(path, values[field])
    put('chat.llm.model', [values['model'], *values['fallback_models']])
    put('chat.prompt.persona', {'default': values['persona'], **values['personas']})
    return result


class ConfigFile:
    def __init__(self, path):
        self.path = Path(path)
        self.stamp = None

    def read(self):
        # 无效文件也记录本次时间戳，避免每次轮询重复报错。调用方保留旧配置，
        # 用户再次保存文件后再尝试解析。
        stamp = self.path.stat().st_mtime_ns
        if stamp == self.stamp:
            return None
        self.stamp = stamp
        return parse_config(yaml.safe_load(self.path.read_text(encoding='utf-8')))

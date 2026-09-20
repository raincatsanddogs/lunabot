from __future__ import annotations

import asyncio
import copy
import math
import time
from dataclasses import asdict, dataclass, field

PROTOCOL_VERSION = 4


@dataclass(frozen=True)
class Scope:
    bot_id: str
    group_id: str

    def __post_init__(self):
        if any(
            not isinstance(v, str) or not v or ':' in v or len(v) > 128
            for v in (self.bot_id, self.group_id)
        ):
            raise ValueError('Invalid scope identifier')

    @property
    def key(self):
        return f"{self.bot_id}:{self.group_id}"


@dataclass
class Event:
    bot_id: str
    group_id: str
    message_id: str
    speaker_id: str
    time: float
    segments: list[dict]
    nickname: str = ""
    seq: int = 0

    @property
    def scope(self):
        return Scope(self.bot_id, self.group_id)

    @property
    def text(self):
        return "".join(
            s.get("data", {}).get("text", "") for s in self.segments if s.get("type") == "text"
        )

    @classmethod
    def from_dict(cls, value):
        fields = {
            k: value[k]
            for k in ("bot_id", "group_id", "message_id", "speaker_id", "time", "segments")
        }
        for key in ("bot_id", "group_id", "message_id", "speaker_id"):
            fields[key] = str(fields[key])
            if not fields[key] or len(fields[key]) > 128:
                raise ValueError(f"Invalid {key}")
        fields["time"] = float(fields["time"])
        if (
            not math.isfinite(fields["time"])
            or abs(fields["time"]) > 253402214400
            or not isinstance(fields["segments"], list)
        ):
            raise ValueError("Invalid event")
        for segment in fields['segments']:
            if (
                not isinstance(segment, dict)
                or not isinstance(segment.get('type'), str)
                or not isinstance(segment.get('data'), dict)
            ):
                raise ValueError('Invalid message segment')
            if segment['type'] == 'text' and not isinstance(segment['data'].get('text'), str):
                raise ValueError('Invalid text segment')
        fields['segments'] = copy.deepcopy(fields['segments'])
        Scope(fields['bot_id'], fields['group_id'])
        return cls(**fields, nickname=str(value.get("nickname", "")), seq=int(value.get("seq", 0)))

    def to_dict(self):
        return asdict(self)


@dataclass
class Settings:
    ambient_p: float = 0.04
    followup_p: float = 0.85
    policy_ttl: int = 120
    attention_seconds: int = 120
    unanswered_limit: int = 2
    debounce: float = 2
    max_batch_wait: float = 5
    wakes_per_minute: int = 3
    calls_per_minute: int = 6
    max_rounds: int = 6
    max_read_calls: int = 4
    max_messages: int = 10
    send_interval_seconds: float = 1
    search_provider: str = ''
    max_search_calls: int = 2
    max_web_read_calls: int = 2
    search_max_results: int = 5
    page_max_chars: int = 6000
    sticker_annotation_model: str = ''
    sticker_prefetch: int = 2
    max_stickers: int = 2
    sticker_cooldown: float = 60
    reply_max_length: int = 512
    input_tokens: int = 24000
    output_tokens: int = 2048
    summary_count: int = 100
    summary_age: int = 1800
    summary_per_hour: int = 2
    media_bytes: int = 2 * 1024**3
    media_file_bytes: int = 20 * 1024**2
    original_days: int = 7
    preview_days: int = 30
    seed: int = 42
    persona: str = "你以固定人设作为群友参与日常聊天。简短自然；不适合参与时保持沉默。"
    personas: dict[str, str] = field(default_factory=dict)
    model: str = "main"
    summary_model: str = ""
    embedding_model: str = ""
    vision_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    image_token_reserve: int = 4096
    timeout: float = 120
    trigger_keywords: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for name in ('ambient_p', 'followup_p'):
            if not math.isfinite(getattr(self, name)) or not 0 <= getattr(self, name) <= 1:
                raise ValueError(f'Invalid {name}')
        for name in (
            'policy_ttl',
            'attention_seconds',
            'unanswered_limit',
            'wakes_per_minute',
            'calls_per_minute',
            'max_rounds',
            'max_read_calls',
            'max_messages',
            'reply_max_length',
            'input_tokens',
            'output_tokens',
            'summary_count',
            'summary_age',
            'summary_per_hour',
            'media_bytes',
            'media_file_bytes',
            'image_token_reserve',
            'max_search_calls',
            'max_web_read_calls',
            'search_max_results',
            'page_max_chars',
            'max_stickers',
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f'Invalid {name}')
        if (
            self.input_tokens < 2048
            or self.debounce < 0
            or self.max_batch_wait < self.debounce
        ):
            raise ValueError('Invalid scheduler/context limits')
        for name in ('send_interval_seconds', 'sticker_cooldown'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'Invalid {name}')
        if type(self.sticker_prefetch) is not int or not 0 <= self.sticker_prefetch <= 4:
            raise ValueError('Invalid sticker_prefetch')
        if self.search_max_results > 5 or self.page_max_chars > 12000:
            raise ValueError('Invalid web result limits')
        if not isinstance(self.trigger_keywords, dict) or any(
            not isinstance(k, str) or not k or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for k, v in self.trigger_keywords.items()
        ):
            raise ValueError('Invalid trigger_keywords')


class Clock:
    def now(self):
        return time.time()

    async def sleep(self, seconds):
        await asyncio.sleep(max(0, seconds))


class ManualClock(Clock):
    def __init__(self, now=0.0):
        self.value = float(now)
        self.changed = asyncio.Event()

    def now(self):
        return self.value

    def advance(self, value):
        value = float(value)
        if not math.isfinite(value) or value < self.value:
            raise ValueError("Logical time must be finite and monotonic")
        self.value = value
        self.changed.set()
        self.changed = asyncio.Event()

    async def sleep(self, seconds):
        until = self.now() + seconds
        while self.now() < until:
            await self.changed.wait()

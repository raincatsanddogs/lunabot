"""+1 复读机核心解析与状态逻辑，不依赖 NoneBot 运行时环境。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import random
from typing import Any


DEFAULT_INTERRUPT_TEXTS = [
    "打断！",
    "打破复读机",
    "禁止套娃",
    "不许复读！",
    "复读机已损坏",
    "打断复读",
]


@dataclass
class GroupRepeatState:
    current_key: Any = None
    raw_message: Any = None
    user_ids: set[int] = field(default_factory=set)
    has_repeated: bool = False

    def reset(self):
        self.current_key = None
        self.raw_message = None
        self.user_ids.clear()
        self.has_repeated = False


def normalize_segments(message: Iterable) -> list[dict]:
    """
    将消息对象统一格式化为 [{'type': str, 'data': dict}] 列表
    """
    segments = []
    for seg in message:
        if isinstance(seg, Mapping):
            kind, data = seg['type'], dict(seg.get('data', {}))
        else:
            kind, data = getattr(seg, 'type', ''), dict(getattr(seg, 'data', {}))
        segments.append({'type': kind, 'data': data})
    return segments


def get_image_key(data: dict) -> str:
    """
    提取图片/表情包的唯一指纹
    """
    if file_unique := data.get('file_unique'):
        return str(file_unique)
    if url := data.get('url'):
        if 'fileid=' in url:
            start = url.find('fileid=') + len('fileid=')
            end = url.find('&', start)
            if end == -1:
                end = len(url)
            return url[start:end]
        return url.split('?')[0]
    if file := data.get('file'):
        return str(file)
    return ""


def extract_repeat_content(
    message: Iterable,
    plain_text: str,
    command_prefix: str = '#',
) -> tuple[tuple[str, str], list[dict]] | None:
    """
    检查消息是否符合复读要求，并提取特征指纹与消息段。
    仅支持：纯文本、单个QQ表情、单张图片/表情包。
    自动过滤包含 @、回复或指令前缀的消息。
    """
    segments = normalize_segments(message)

    for seg in segments:
        if seg['type'] in ('at', 'reply', 'forward', 'xml', 'json', 'video', 'record', 'poke'):
            return None

    cleaned_text = plain_text.strip()
    prefixes = (command_prefix, '/', '#')
    if any(cleaned_text.startswith(p) for p in prefixes):
        return None

    # 过滤空纯文本段
    non_empty_segs = []
    for seg in segments:
        if seg['type'] == 'text':
            if seg['data'].get('text', '').strip():
                non_empty_segs.append(seg)
        else:
            non_empty_segs.append(seg)

    if not non_empty_segs:
        return None

    # 1. 纯文本
    if all(seg['type'] == 'text' for seg in non_empty_segs):
        full_text = "".join(seg['data'].get('text', '') for seg in non_empty_segs).strip()
        if not full_text:
            return None
        return (('text', full_text), [{'type': 'text', 'data': {'text': full_text}}])

    # 2. 单个 QQ 表情
    if len(non_empty_segs) == 1 and non_empty_segs[0]['type'] == 'face':
        face_id = str(non_empty_segs[0]['data'].get('id', ''))
        if not face_id:
            return None
        return (('face', face_id), [non_empty_segs[0]])

    # 3. 单张图片 / 表情包
    if len(non_empty_segs) == 1 and non_empty_segs[0]['type'] in ('image', 'mface'):
        seg = non_empty_segs[0]
        img_key = get_image_key(seg['data'])
        if not img_key:
            return None
        return (('image', img_key), [seg])

    return None


def handle_message_step(
    state: GroupRepeatState,
    user_id: int,
    repeat_item: tuple[tuple[str, str], Any] | None,
    threshold: int = 3,
    interrupt_prob: float = 0.1,
    random_val: float | None = None,
) -> tuple[str, Any] | None:
    """
    处理单步消息状态更新，并判定是否触发复读或打断。
    返回值：
      - None: 未触发动作
      - ('repeat', raw_message): 触发正常复读
      - ('interrupt', raw_message): 触发打断复读
    """
    if repeat_item is None:
        state.reset()
        return None

    key, raw_msg = repeat_item

    if key != state.current_key:
        state.current_key = key
        state.raw_message = raw_msg
        state.user_ids = {user_id}
        state.has_repeated = False
        return None

    state.user_ids.add(user_id)
    state.raw_message = raw_msg

    if len(state.user_ids) >= threshold and not state.has_repeated:
        state.has_repeated = True
        roll = random.random() if random_val is None else random_val
        if interrupt_prob > 0 and roll < interrupt_prob:
            return ('interrupt', state.raw_message)
        return ('repeat', state.raw_message)

    return None


def choose_interrupt_content(
    texts: list[str] | None,
    text_weight: float,
    gallery_img_cq: str | None,
    image_weight: float,
    rng: random.Random | None = None,
) -> Any:
    """
    按权重在文本和画廊图片中选取打断内容
    """
    r = rng or random
    candidate_texts = texts if texts else DEFAULT_INTERRUPT_TEXTS

    choices: list[tuple[str, Any]] = []
    weights: list[float] = []

    if candidate_texts and text_weight > 0:
        choices.append(('text', r.choice(candidate_texts)))
        weights.append(float(text_weight))

    if gallery_img_cq and image_weight > 0:
        choices.append(('image', gallery_img_cq))
        weights.append(float(image_weight))

    if not choices:
        return r.choice(DEFAULT_INTERRUPT_TEXTS)

    chosen = r.choices(choices, weights=weights, k=1)[0]
    return chosen[1]

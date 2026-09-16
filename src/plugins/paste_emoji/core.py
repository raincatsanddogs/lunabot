"""贴表情的解析与状态逻辑，不依赖 NoneBot 的启动环境。

表情 ID 的转换规则参考 Bluemangoo/nonebot_plugin_paste_emoji：
https://github.com/Bluemangoo/nonebot_plugin_paste_emoji
"""

from collections.abc import Iterable, Mapping
import re
import unicodedata


PASTE_COMMANDS = ['/贴表情', '/paste_face', '/paste-face']
START_COMMANDS = ['/自㊗️餐', '/自㊗餐']
STOP_COMMANDS = [
    f'/{prefix}自{emoji}餐'
    for prefix in ('停止', '结束', '关闭')
    for emoji in ('㊗️', '㊗')
]
BUFFET_EMOJI_ID = '12951'
MAX_EMOJI_ID = 2 ** 32 - 1


def message_segments(message: Iterable) -> list[dict]:
    """复制消息段，并合并相邻文本；不会修改原始事件。"""
    segments = []
    for segment in message:
        if isinstance(segment, Mapping):
            kind, data = segment['type'], dict(segment['data'])
        else:
            kind, data = segment.type, dict(segment.data)
        if kind == 'text' and segments and segments[-1]['type'] == 'text':
            segments[-1]['data']['text'] += data.get('text', '')
        else:
            segments.append({'type': kind, 'data': data})
    return segments


def command_segments(message: Iterable, command: str) -> list[dict]:
    """移除命令及其前面的回复、提及，保留参数中的非文本消息段。"""
    segments = message_segments(message)
    plain_text = ''.join(s['data'].get('text', '') for s in segments if s['type'] == 'text')
    start = plain_text.find(command)
    if start < 0:
        raise ValueError('无法解析指令参数，请重新发送指令')
    remaining = start + len(command)
    result = []
    for segment in segments:
        if segment['type'] == 'text' and remaining:
            text = segment['data'].get('text', '')
            skipped = min(remaining, len(text))
            remaining -= skipped
            segment['data']['text'] = text[skipped:]
            if segment['data']['text']:
                result.append(segment)
        elif not remaining:
            result.append(segment)
    return result


def leading_command(message: Iterable, commands: Iterable[str]) -> str | None:
    """只跳过开头的回复、提及和空白，不把正文中的命令当作调用。"""
    for segment in message_segments(message):
        if segment['type'] in ('reply', 'at'):
            continue
        if segment['type'] != 'text':
            return None
        text = segment['data'].get('text', '').lstrip()
        if not text:
            continue
        return next((cmd for cmd in sorted(commands, key=len, reverse=True) if text.startswith(cmd)), None)
    return None


def parse_emoji_id(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None

    if re.fullmatch(r'[0-9]+', value):
        digits = value.lstrip('0') or '0'
        # 先限制位数，避免过长数字触发 Python 的整数转换限制。
        if len(digits) > 10:
            return None
        number = int(digits)
    else:
        lower = value.lower()
        hex_match = re.fullmatch(r'(?:u\+|0x|u)([0-9a-f]+)|([0-9a-f]+)h', lower)
        if hex_match:
            digits = (hex_match[1] or hex_match[2]).lstrip('0') or '0'
            if len(digits) > 8:
                return None
            number = int(digits, 16)
        elif lower.startswith(('u+', '0x', 'u')) or lower.endswith('h'):
            return None
        else:
            # 与参考插件一致：组合表情使用首个码点，具体可用性由 QQ 决定。
            number = ord(value[0])
            if number <= 256 or value[0].isnumeric():
                return None
            if unicodedata.category(value[0]) in ('Mn', 'Mc', 'Me', 'Cc', 'Cf', 'Cs'):
                return None
    return str(number) if 0 <= number <= MAX_EMOJI_ID else None


def parse_emoji_arguments(segments: Iterable) -> tuple[list[str], list[str]]:
    emoji_ids, invalid = [], []
    seen = set()
    for segment in message_segments(segments):
        kind, data = segment['type'], segment['data']
        if kind == 'text':
            tokens = data.get('text', '').split()
        elif kind == 'face':
            value = str(data.get('id', ''))
            if not re.fullmatch(r'[0-9]+', value):
                invalid.append(f'QQ表情({value or "缺少ID"})')
                continue
            tokens = [value]
        else:
            continue
        for token in tokens:
            emoji_id = parse_emoji_id(token)
            if emoji_id is None:
                invalid.append(token)
            elif emoji_id not in seen:
                seen.add(emoji_id)
                emoji_ids.append(emoji_id)
    return emoji_ids, invalid


def target_message_id(event) -> int:
    if event.reply is not None:
        return int(event.reply.message_id)
    # 仓库的回复预处理在 get_msg 失败时保留 reply 段，仍应使用它的 ID。
    for segment in message_segments(event.message):
        if segment['type'] == 'reply':
            try:
                return int(segment['data']['id'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('无法识别回复的消息，请重新回复后发送指令') from None
    return int(event.message_id)


def buffet_targets(segments: Iterable, actor_id: int) -> list[int]:
    targets = []
    for segment in message_segments(segments):
        kind, data = segment['type'], segment['data']
        if kind == 'at':
            qq = str(data.get('qq', ''))
            if qq == 'all':
                raise ValueError('自㊗️餐不支持 @全体成员，请指定具体群成员')
            if not re.fullmatch(r'[0-9]{1,20}', qq) or int(qq) == 0:
                raise ValueError('无法识别指定的群成员，请使用 @成员')
            target = int(qq)
            if target not in targets:
                targets.append(target)
        elif kind != 'text' or data.get('text', '').strip():
            raise ValueError('不带参数操作自己，或在指令后使用 @成员 指定目标')
    return targets or [int(actor_id)]


async def check_buffet_permission(bot, event, targets: list[int], is_superuser: bool):
    if is_superuser or all(target == int(event.user_id) for target in targets):
        return
    role = getattr(event.sender, 'role', None)
    if not role:
        try:
            info = await bot.call_api(
                'get_group_member_info',
                group_id=int(event.group_id), user_id=int(event.user_id), no_cache=True,
            )
            role = info.get('role')
        except Exception:
            raise PermissionError('无法确认群管理权限，请稍后重试') from None
    if role not in ('owner', 'admin'):
        raise PermissionError('只有群主、群管理员或 bot 超管可以为其他成员开关自㊗️餐')


class BuffetState:
    """按 bot、群、用户隔离的进程内名单；不写入 FileDB。"""

    def __init__(self):
        self.members: set[tuple[str, int, int]] = set()

    def contains(self, bot_id, group_id, user_id) -> bool:
        return (str(bot_id), int(group_id), int(user_id)) in self.members

    def set_enabled(self, bot_id, group_id, targets: list[int], enabled: bool):
        for user_id in targets:
            key = (str(bot_id), int(group_id), int(user_id))
            if enabled:
                self.members.add(key)
            else:
                self.members.discard(key)


async def set_reaction(bot, message_id: int, emoji_id: str):
    result = await bot.call_api(
        'set_msg_emoji_like', message_id=message_id, emoji_id=emoji_id, set=True,
    )
    # LLOneBot/SnowLuma 的成功 data 可以为空；NapCat 可能返回底层结果码。
    if isinstance(result, dict):
        code = result.get('result')
        if code is False or (type(code) is int and code != 0):
            raise RuntimeError(result.get('errMsg') or '接入端未能添加表情回应')


async def paste_reactions(bot, message_id: int, emoji_ids: list[str]) -> list[tuple[str, Exception]]:
    failures = []
    for emoji_id in emoji_ids:
        try:
            await set_reaction(bot, message_id, emoji_id)
        except Exception as exc:
            failures.append((emoji_id, exc))
    return failures

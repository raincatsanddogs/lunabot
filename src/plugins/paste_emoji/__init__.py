from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.adapters.onebot.v11.message import escape
from nonebot.rule import Rule

from ..utils import (
    CmdHandler, HandlerContext, ColdDown, ReplyException,
    get_logger, get_file_db, get_group_black_list, get_exc_desc, truncate,
    check_group_disabled, check_in_blacklist, check_self, check_superuser, on_safe_mode,
)
from .core import (
    PASTE_COMMANDS, START_COMMANDS, STOP_COMMANDS, BUFFET_EMOJI_ID,
    BuffetState, command_segments, leading_command, parse_emoji_arguments, target_message_id,
    buffet_targets, check_buffet_permission, set_reaction, paste_reactions,
)


logger = get_logger('PasteEmoji')
file_db = get_file_db('data/paste_emoji/db.json', logger)
gbl = get_group_black_list(file_db, logger, 'paste_emoji')
cd = ColdDown(file_db, logger)
buffet = BuffetState()

USAGE = '用法：/贴表情 表情ID或emoji（多个用空格分隔，也可插入QQ表情）\n例如：/贴表情 14 👍 U+3297\n回复消息时贴到回复目标，否则贴到指令消息。'


class EmojiCmdHandler(CmdHandler):
    def __init__(self, commands: list[str]):
        super().__init__(commands, logger, use_seg_cmd=False)

        async def match_command(event: GroupMessageEvent) -> bool:
            return leading_command(event.message, self.commands) is not None

        # 仓库会保留回复前的 @，get_msg 失败时也会保留 reply 段。
        # NoneBot 默认 command 规则只读取首个文本段；本地规则兼容这些前缀，
        # 保留原消息供目标解析，同时继续使用 CmdHandler 的权限、冷却和帮助。
        self.handler.rule = Rule(match_command)


paste = EmojiCmdHandler(PASTE_COMMANDS)
paste.check_group().check_wblist(gbl, allow_super=False).check_cdrate(cd)
@paste.handle()
async def handle_paste(ctx: HandlerContext):
    try:
        segments = command_segments(ctx.event.message, ctx.trigger_cmd)
        emoji_ids, invalid = parse_emoji_arguments(segments)
        message_id = target_message_id(ctx.event)
    except ValueError as exc:
        raise ReplyException(str(exc)) from exc

    failures = await paste_reactions(ctx.bot, message_id, emoji_ids)
    for emoji_id, exc in failures:
        logger.warning(f'贴表情失败 bot={ctx.bot.self_id} msg={message_id} emoji={emoji_id}: {get_exc_desc(exc)}')

    messages = []
    if invalid:
        preview = '、'.join(escape(truncate(value, 32)) for value in invalid[:8])
        suffix = f'（共{len(invalid)}项）' if len(invalid) > 8 else ''
        messages.append(f'无法解析的表情：{preview}{suffix}')
    if failures:
        preview = '、'.join(emoji_id for emoji_id, _ in failures[:8])
        messages.append(f'贴表情失败：{preview}。请确认表情受QQ支持、目标消息仍有效，且接入端支持贴表情接口。')
    if not emoji_ids:
        messages.append(USAGE)
    if messages:
        await ctx.asend_reply_msg('\n'.join(messages))


async def change_buffet(ctx: HandlerContext, enabled: bool):
    try:
        targets = buffet_targets(command_segments(ctx.event.message, ctx.trigger_cmd), ctx.user_id)
        await check_buffet_permission(ctx.bot, ctx.event, targets, check_superuser(ctx.event))
    except (ValueError, PermissionError) as exc:
        raise ReplyException(str(exc)) from exc

    # 整批检查权限后再更新，避免 @自己 @他人 时出现部分生效。
    buffet.set_enabled(ctx.bot.self_id, ctx.group_id, targets, enabled)
    target_text = '你' if targets == [ctx.user_id] else '、'.join(str(uid) for uid in targets)
    if enabled:
        await ctx.asend_reply_msg(f'已为{target_text}开启自㊗️餐，用餐愉快！\n发送 /停止自㊗️餐 退出，bot重启后自动停止。')
    else:
        await ctx.asend_reply_msg(f'已为{target_text}停止自㊗️餐，感谢光临！')


start = EmojiCmdHandler(START_COMMANDS)
start.check_group().check_wblist(gbl, allow_super=False).check_cdrate(cd)
@start.handle()
async def handle_start(ctx: HandlerContext):
    await change_buffet(ctx, True)


stop = EmojiCmdHandler(STOP_COMMANDS)
stop.check_group().check_wblist(gbl, allow_super=False)
@stop.handle()
async def handle_stop(ctx: HandlerContext):
    await change_buffet(ctx, False)


# 在常规指令（priority=0）前运行，且不阻断其他插件。
auto_paste = on_message(priority=-1, block=False)


def is_service_command(event: GroupMessageEvent) -> bool:
    commands = PASTE_COMMANDS + START_COMMANDS + STOP_COMMANDS
    # 群服务管理使用 CmdHandler 默认的空格/下划线/无分隔符变体。
    commands += [f'/paste{sep}emoji{sep}{action}' for sep in ('', ' ', '_') for action in ('on', 'off', 'status')]
    commands += [f'/paste_emoji {action}' for action in ('on', 'off', 'status')]
    commands += ['/help paste_emoji', '/帮助 paste_emoji']
    return leading_command(event.message, commands) is not None


@auto_paste.handle()
async def handle_auto_paste(bot: Bot, event: GroupMessageEvent):
    if check_self(event) or not buffet.contains(bot.self_id, event.group_id, event.user_id):
        return
    if check_group_disabled(event.group_id) or not gbl.check_id(event.group_id):
        return
    if check_in_blacklist(event.user_id) or (on_safe_mode() and not check_superuser(event)):
        return
    if is_service_command(event):
        return
    try:
        await set_reaction(bot, int(event.message_id), BUFFET_EMOJI_ID)
    except Exception as exc:
        logger.warning(f'自动贴㊗️失败 bot={bot.self_id} group={event.group_id} msg={event.message_id}: {get_exc_desc(exc)}')

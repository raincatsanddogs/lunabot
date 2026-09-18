from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment

from ..utils import (
    Config,
    CmdHandler,
    HandlerContext,
    get_logger,
    get_file_db,
    get_group_white_list,
    get_command_prefix,
    get_image_cq,
    get_exc_desc,
    check_self,
    check_group_disabled,
    check_in_blacklist,
    check_is_banned_msg,
    check_superuser,
    on_safe_mode,
    is_group_msg,
    send_group_msg_by_bot,
)
from .core import (
    DEFAULT_INTERRUPT_TEXTS,
    GroupRepeatState,
    choose_interrupt_content,
    extract_repeat_content,
    handle_message_step,
)

config = Config('repeater')
logger = get_logger('Repeater')
file_db = get_file_db('data/repeater/db.json', logger)

group_states: dict[int, GroupRepeatState] = {}


def on_group_turn_off(group_id: int):
    group_states.pop(group_id, None)


# 注册标准服务白名单管理（默认关闭，仅超管可开关）
gwl = get_group_white_list(file_db, logger, 'repeater', off_func=on_group_turn_off)


# 别名指令支持（/repeat on/off/status, /复读 on/off/status）
alias_on = CmdHandler(['/repeat on', '/复读 on'], logger)
alias_on.check_superuser()
@alias_on.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    target_gid = int(args) if args.isdigit() else ctx.group_id
    if gwl.add(target_gid):
        await ctx.asend_reply_msg(f'成功开启群 {target_gid} 的复读机服务')
    else:
        await ctx.asend_reply_msg(f'群 {target_gid} 的复读机服务已经是开启状态')


alias_off = CmdHandler(['/repeat off', '/复读 off'], logger)
alias_off.check_superuser()
@alias_off.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    target_gid = int(args) if args.isdigit() else ctx.group_id
    if gwl.remove(target_gid):
        await ctx.asend_reply_msg(f'成功关闭群 {target_gid} 的复读机服务')
    else:
        await ctx.asend_reply_msg(f'群 {target_gid} 的复读机服务已经是关闭状态')


alias_status = CmdHandler(['/repeat status', '/复读 status', '/复读 状态'], logger)
@alias_status.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    target_gid = int(args) if args.isdigit() else ctx.group_id
    status_text = "开启中" if gwl.check_id(target_gid) else "关闭中"
    await ctx.asend_reply_msg(f'群 {target_gid} 的复读机服务{status_text}')


async def resolve_gallery_image(gall_name: str) -> str | None:
    """
    尝试从画廊服务指定画廊中抽选一张图片
    """
    try:
        from ..gallery import GalleryManager
        gall = GalleryManager.get().find_gall(gall_name)
        if gall and gall.pics:
            import random
            pic = random.choice(gall.pics)
            return await get_image_cq(pic.path, send_url_as_is=True)
    except Exception as e:
        logger.warning(f"获取打断画廊 [{gall_name}] 图片失败: {get_exc_desc(e)}")
    return None


async def get_interrupt_content() -> Message | str:
    """
    获取打断复读内容（文本或画廊图片，按配置权重抽选）
    """
    texts = config.get('interrupt_texts', DEFAULT_INTERRUPT_TEXTS) or DEFAULT_INTERRUPT_TEXTS
    text_weight = config.get('text_weight', 1)
    image_weight = config.get('image_weight', 1)
    gall_name = config.get('interrupt_gallery', '打断')

    gallery_img_cq = None
    if image_weight > 0:
        gallery_img_cq = await resolve_gallery_image(gall_name)

    return choose_interrupt_content(
        texts=texts,
        text_weight=text_weight,
        gallery_img_cq=gallery_img_cq,
        image_weight=image_weight,
    )


repeater_matcher = on_message(priority=99, block=False)


@repeater_matcher.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    if check_self(event):
        return
    if not is_group_msg(event):
        return

    group_id = event.group_id
    if check_group_disabled(group_id):
        return
    if not gwl.check_id(group_id):
        return
    if check_in_blacklist(event.user_id) or check_is_banned_msg(event.message.extract_plain_text()) or (on_safe_mode() and not check_superuser(event)):
        return

    if group_id not in group_states:
        group_states[group_id] = GroupRepeatState()
    state = group_states[group_id]

    plain_text = event.message.extract_plain_text()
    prefix = get_command_prefix()
    repeat_item = extract_repeat_content(event.message, plain_text, prefix)

    # 构造待发送 Message
    msg_to_send = None
    if repeat_item is not None:
        key, segs = repeat_item
        msg_to_send = Message([MessageSegment(seg['type'], seg['data']) for seg in segs])
        repeat_item = (key, msg_to_send)

    threshold = config.get('repeat_threshold', 3)
    interrupt_prob = config.get('interrupt_probability', 0.1)

    action = handle_message_step(
        state=state,
        user_id=event.user_id,
        repeat_item=repeat_item,
        threshold=threshold,
        interrupt_prob=interrupt_prob,
    )

    if action is None:
        return

    action_type, message = action
    try:
        if action_type == 'interrupt':
            content = await get_interrupt_content()
            logger.info(f"群 {group_id} 触发打断复读: {content}")
            await send_group_msg_by_bot(group_id, content, bot=bot)
        else:
            logger.info(f"群 {group_id} 触发跟随复读: {message}")
            await send_group_msg_by_bot(group_id, message, bot=bot)
    except Exception as e:
        logger.print_exc(f"群 {group_id} 复读发送失败")

import importlib
import importlib.util
import re
import sys
import types
from datetime import datetime
from typing import Any

from nonebot import get_bot, get_driver, on_message, on_notice
from nonebot.adapters.minecraft import (
    Bot as MinecraftBot,
    PlayerAchievementEvent,
    PlayerChatEvent,
    PlayerDeathEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
)
from nonebot.rule import Rule

try:
    from nonebot.adapters.qq import AuditException
    from nonebot.adapters.qq import Bot as QQBot
except Exception:
    AuditException = Exception
    QQBot = None

from ..utils import *
from .config_bridge import (
    LISTEN_MODES,
    apply_chatprefix,
    build_group_defaults,
    build_guild_defaults,
    build_mc_driver_config,
    normalize_listen_mode,
    should_ignore_message_header,
    should_sync_messages,
)


config = Config('mc')
logger = get_logger('MC')
file_db = get_file_db('data/mc/db.json', logger)
cd = ColdDown(file_db, logger)

raw_config = config.get_all()
status_config = raw_config.get('status', {}) if isinstance(raw_config, dict) else {}
mc_enabled = bool(raw_config and raw_config.get('enabled', True))

QUERY_INTERVAL = int(status_config.get("query_interval", 2))
QUERY_OFFSET = int(status_config.get("query_offset", -1000))
DISCONNECT_NOTIFY_COUNT = int(status_config.get("disconnect_notify_count", 60))
MCQQ_ALIAS = "_lunabot_mcqq"


def _set_driver_config(key: str, value: Any):
    # mcqq 的配置读取发生在模块导入期，因此需要先写入 NoneBot driver config。
    driver_config = get_driver().config
    try:
        setattr(driver_config, key, value)
    except Exception:
        object.__setattr__(driver_config, key, value)
    if hasattr(driver_config, "__dict__"):
        driver_config.__dict__[key] = value


def _ensure_mcqq_alias_package():
    # 直接导入 nonebot_plugin_mcqq 会执行官方 __init__ 并注册 /mcc 等命令。
    # 用别名包加载子模块，可以复用工具代码，同时避开官方命令 matcher。
    if MCQQ_ALIAS in sys.modules:
        return

    spec = importlib.util.find_spec("nonebot_plugin_mcqq")
    if spec is None or spec.submodule_search_locations is None:
        raise RuntimeError("未安装 nonebot-plugin-mcqq")

    package = types.ModuleType(MCQQ_ALIAS)
    package.__path__ = list(spec.submodule_search_locations)
    package.__package__ = MCQQ_ALIAS
    sys.modules[MCQQ_ALIAS] = package


def _import_mcqq_module(module_name: str):
    _ensure_mcqq_alias_package()
    return importlib.import_module(f"{MCQQ_ALIAS}.{module_name}")


def gametick2time(tick):
    tick = tick % 24000
    hour = int(tick // 1000 + 6) % 24
    minute = (tick % 1000) // 100 * 6
    return f'{hour:02}:{minute:02}'


class GroupState:
    # 一个 QQ 群对应一份可被命令修改的 MC 状态；旧 data/mc/db.json 仍作为覆盖层使用。
    def __init__(self, defaults: dict[str, Any]):
        self.server_name = defaults["server_name"]
        self.group_id = str(defaults["group_id"])
        self.adapter = defaults["adapter"]
        self.bot_id = defaults["bot_id"]
        self.default = defaults

        self.load()

        self.first_update = True
        self.failed_count = 0
        self.failed_time = None
        self.last_failed_reason = None
        self.has_success_query = False
        self.next_query_ts = 0
        self.players = {}
        self.time = 0
        self.storming = False
        self.thundering = False

    @property
    def db_key(self):
        return f"{self.group_id}.server_info"

    def load(self):
        # YAML 给默认值，文件 DB 保存群内命令修改后的运行状态。
        data = file_db.get(self.db_key, {})
        self.url = data.get("url", self.default.get("url", ""))
        raw_listen_mode = data.get("listen_mode", self.default.get("listen_mode", "off"))
        try:
            self.listen_mode = normalize_listen_mode(raw_listen_mode)
        except ValueError:
            # DB 中的未知旧值不阻断启动，回退到 off；log 会在 normalize 中兼容为 queqiao。
            self.listen_mode = "off"
        self.info = data.get("info", self.default.get("info", ""))
        self.chatprefix = data.get("chatprefix", self.default.get("chatprefix", "[Server] "))
        self.notify_on = data.get("notify_on", self.default.get("notify_on", True))

    def save(self):
        data = file_db.get(self.db_key, {})
        data.update({
            "url": self.url,
            "listen_mode": self.listen_mode,
            "info": self.info,
            "chatprefix": self.chatprefix,
            "notify_on": self.notify_on,
        })
        file_db.set(self.db_key, data)

    def format_to_group(self, text: str):
        return apply_chatprefix(text, self.chatprefix)

    async def query_dynamicmap(self):
        url = f"{self.url}/up/world/world/{self.next_query_ts}"
        async with get_client_session().get(url, verify_ssl=False) as resp:
            data = await resp.text()
            if resp.status != 200:
                raise Exception(data)
            return loads_json(data)

    async def update_status(self):
        # queqiao 的聊天和事件由 Minecraft adapter 推送；这里只轮询 dynamicmap 状态。
        if self.listen_mode != "dynamicmap":
            return

        data = await self.query_dynamicmap()
        current_ts = int(data["timestamp"])
        self.next_query_ts = int(current_ts + QUERY_INTERVAL * 1000 + QUERY_OFFSET)

        self.time = data["servertime"]
        self.storming = data["hasStorm"]
        self.thundering = data["isThundering"]
        self.players = {player["account"]: player for player in data["players"]}
        self.first_update = False


def _load_group_states():
    if not mc_enabled:
        return {}
    return {
        key: GroupState(defaults)
        for key, defaults in build_group_defaults(raw_config).items()
    }


group_states = _load_group_states()
onebot_group_states = {
    state.group_id: state
    for state in group_states.values()
    if state.adapter == "onebot"
}
states_by_server: dict[str, list[GroupState]] = {}
for state in group_states.values():
    states_by_server.setdefault(state.server_name, []).append(state)
guild_defaults = build_guild_defaults(raw_config) if mc_enabled else []

mcqq_send_to_mc = None
mcqq_data_source = None

if not raw_config:
    logger.info("未找到 config/mc.yaml 或配置为空，跳过加载 MC 互通")
elif mc_enabled:
    driver_config = build_mc_driver_config(raw_config)
    for key, value in driver_config.items():
        _set_driver_config(key, value)

    _import_mcqq_module("bot_manage")
    mcqq_send_to_mc = _import_mcqq_module("utils.send_to_mc")
    mcqq_data_source = _import_mcqq_module("data_source")
    logger.info("已通过 config/mc.yaml 初始化 mcqq 驱动版 MC 插件")
else:
    logger.info("MC 插件已禁用")


def _normalize_mc_white_list():
    # 通用 GroupWhiteList 用 int 群号做判断；旧数据如果写成字符串，先在加载时转正。
    key = "group_white_list_mc"
    white_list = file_db.get(key, [])
    if not isinstance(white_list, list):
        file_db.set(key, [])
        return

    normalized = []
    changed = False
    for value in white_list:
        item = value
        if isinstance(value, str) and value.strip().isdigit():
            item = int(value.strip())
            changed = True
        if item in normalized:
            changed = True
            continue
        normalized.append(item)

    if changed:
        file_db.set(key, normalized)


_normalize_mc_white_list()
# 接回 lunabot 通用服务开关，自动注册 /mc on、/mc off、/mc status。
gwl = get_group_white_list(file_db, logger, "mc")


def _is_onebot_group_enabled(group_id: Any) -> bool:
    # 消息互通也跟随 /mc on/off；这样开关语义与其他白名单类服务一致。
    try:
        return gwl.check_id(int(group_id))
    except (TypeError, ValueError):
        return gwl.check_id(group_id)


def _is_group_state_enabled(state: GroupState) -> bool:
    # 目前通用群白名单面向 OneBot 群号；QQ 官方群/频道仍以 YAML 绑定为准。
    if state.adapter == "onebot":
        return _is_onebot_group_enabled(state.group_id)
    return True


def _is_queqiao_group_enabled(state: GroupState) -> bool:
    # 双向互通需要同时打开 /mc 服务开关，并把本群监听模式设为 queqiao。
    return _is_group_state_enabled(state) and should_sync_messages(state.listen_mode)


def get_group_state(group_id: int | str, raise_exc=True) -> GroupState | None:
    state = onebot_group_states.get(str(group_id))
    if state is None and raise_exc:
        raise Exception(f"群 {group_id} 没有在 config/mc.yaml 中绑定 MC 服务器")
    return state


def _event_has_mc_binding(event) -> bool:
    if mcqq_data_source is None:
        return False
    group_id = getattr(event, "group_id", None)
    group_openid = getattr(event, "group_openid", None)
    channel_id = getattr(event, "channel_id", None)
    if str(group_id) in mcqq_data_source.ONEBOT_GROUP_SERVER_DICT:
        state = get_group_state(group_id, raise_exc=False)
        return state is not None and _is_queqiao_group_enabled(state)
    if str(group_openid) in mcqq_data_source.QQ_GROUP_SERVER_DICT:
        state = group_states.get(f"qq:{group_openid}")
        return state is not None and _is_queqiao_group_enabled(state)
    return (
        str(channel_id) in mcqq_data_source.QQ_GUILD_SERVER_DICT
    )


def _qq_to_mc_rule(event) -> bool:
    # 本地注册普通消息同步；带忽略前缀的 lunabot 指令不进入 MC。
    if not _event_has_mc_binding(event):
        return False

    try:
        text = event.get_plaintext()
    except Exception:
        text = str(event.get_message())

    return not should_ignore_message_header(
        raw_config,
        text,
        group_id=getattr(event, "group_id", None),
        group_openid=getattr(event, "group_openid", None),
        channel_id=getattr(event, "channel_id", None),
    )


if mc_enabled and mcqq_send_to_mc is not None:
    qq_to_mc = on_message(
        priority=int(raw_config.get("command_priority", 98)) + 1,
        block=False,
        rule=Rule(_qq_to_mc_rule),
    )

    @qq_to_mc.handle()
    async def _(bot, event):
        await mcqq_send_to_mc.send_message_to_target_server(bot=bot, event=event)


def _mc_event_rule(event) -> bool:
    return getattr(event, "server_name", None) in states_by_server


def _strip_mc_color(text: str) -> str:
    return re.sub(r"[&§].", "", text)


async def _send_to_qq_group(state: GroupState, text: str):
    # OneBot 走 lunabot 统一发送封装；QQ 官方适配器直接调用主动推送接口。
    text = state.format_to_group(text)
    if state.adapter == "onebot":
        await send_group_msg_by_bot(int(state.group_id), text)
        return

    if state.adapter == "qq" and QQBot is not None:
        try:
            bot = get_bot(state.bot_id)
            if isinstance(bot, QQBot):
                await bot.post_group_messages(
                    group_openid=state.group_id,
                    msg_type=0,
                    content=text,
                )
        except AuditException as e:
            logger.debug(f"发送至 QQ Group {state.group_id} 的 MC 消息正在审核中")
            try:
                await e.get_audit_result(3)
            except Exception as audit_error:
                logger.error(f"获取 QQ Group {state.group_id} 审核结果失败: {audit_error!r}")
        except Exception as e:
            logger.error(f"发送至 QQ Group {state.group_id} 失败: {e!r}")


async def _send_to_qq_guild(guild: dict[str, Any], text: str):
    if QQBot is None:
        return
    try:
        bot = get_bot(guild["bot_id"])
        if isinstance(bot, QQBot):
            await bot.send_to_channel(channel_id=guild["channel_id"], message=text)
    except AuditException as e:
        logger.debug(f"发送至 QQ Channel {guild['channel_id']} 的 MC 消息正在审核中")
        try:
            await e.get_audit_result(3)
        except Exception as audit_error:
            logger.error(f"获取 QQ Channel {guild['channel_id']} 审核结果失败: {audit_error!r}")
    except Exception as e:
        logger.error(f"发送至 QQ Channel {guild['channel_id']} 失败: {e!r}")


async def send_mc_msg_to_qq(server_name: str, text: str):
    # 官方发送工具只能加全局前缀；这里按群配置 chatprefix 后逐群发送。
    text = _strip_mc_color(text)
    if raw_config.get("display_server_name", True):
        text = f"[{server_name}] {text}"

    for state in states_by_server.get(server_name, []):
        if not _is_queqiao_group_enabled(state):
            continue
        await _send_to_qq_group(state, text)

    for guild in guild_defaults:
        if guild["server_name"] == server_name:
            await _send_to_qq_guild(guild, text)


if mc_enabled:
    mc_msg = on_message(priority=5, rule=Rule(_mc_event_rule))
    mc_notice = on_notice(priority=4, rule=Rule(_mc_event_rule))

    @mc_msg.handle()
    async def _(event: PlayerChatEvent):
        if not isinstance(event, PlayerChatEvent):
            return
        message_text = str(event.message)
        if message_text.startswith("!!"):
            return
        text = f"{event.player.nickname}{raw_config.get('say_way', '：')}{message_text}"
        await send_mc_msg_to_qq(event.server_name, text)

    @mc_notice.handle()
    async def _(event):
        if isinstance(event, PlayerDeathEvent):
            text = event.death.text or f"{event.player.nickname} 死亡了"
        elif isinstance(event, PlayerJoinEvent):
            text = f"{event.player.nickname} 加入了游戏"
        elif isinstance(event, PlayerQuitEvent):
            text = f"{event.player.nickname} 离开了游戏"
        elif isinstance(event, PlayerAchievementEvent):
            text = (
                event.achievement.translate.text
                if event.achievement.translate and event.achievement.translate.text
                else f"{event.player.nickname} 获得了成就({event.achievement.key})"
            )
        else:
            return
        await send_mc_msg_to_qq(event.server_name, text)


async def notify_server_connection(server_name: str, connected: bool):
    text = f"服务器 [{server_name}] 已成功连接" if connected else f"服务器 [{server_name}] 已断开连接"
    for state in states_by_server.get(server_name, []):
        if state.notify_on and _is_queqiao_group_enabled(state):
            await _send_to_qq_group(state, text)


@get_driver().on_bot_connect
async def _(bot):
    if isinstance(bot, MinecraftBot):
        await notify_server_connection(bot.self_id, True)


@get_driver().on_bot_disconnect
async def _(bot):
    if isinstance(bot, MinecraftBot):
        await notify_server_connection(bot.self_id, False)


async def query_group_status(state: GroupState):
    # 状态查询只服务 /info 和连断通知，不再承担旧插件的聊天转发职责。
    if state.listen_mode != "dynamicmap" or not state.url:
        return

    try:
        await state.update_status()
        if state.failed_count >= DISCONNECT_NOTIFY_COUNT and state.notify_on:
            await _send_to_qq_group(state, "重新建立服务器监听连接")
        state.failed_count = 0
        state.failed_time = None
        state.last_failed_reason = None
        state.has_success_query = True
    except Exception as e:
        if state.failed_count == DISCONNECT_NOTIFY_COUNT and state.has_success_query and state.notify_on:
            await _send_to_qq_group(state, f"监听服务器连接断开: {e}")
            state.failed_time = datetime.now()
        state.failed_count += 1
        state.last_failed_reason = str(e)
        state.next_query_ts = 0
        logger.print_exc(f"群 {state.group_id} 的 MC 状态查询失败: {e}")


@repeat_with_interval(QUERY_INTERVAL, "请求MC状态", logger)
async def query_all_group_status():
    for state in group_states.values():
        if _is_group_state_enabled(state) and state.listen_mode == "dynamicmap" and state.url:
            asyncio.get_event_loop().create_task(query_group_status(state))


def build_info_message(state: GroupState):
    msg = f"【{state.server_name}】\n"
    if state.info.strip():
        msg += state.info.strip()
        msg += "\n------------------------\n"

    if state.listen_mode == "off":
        msg += "监听已关闭"
    elif state.failed_count > 0:
        msg += "服务器监听连接断开\n"
        if state.failed_time:
            msg += f"断连时间: {state.failed_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        if state.last_failed_reason:
            msg += "最近一次错误:\n"
            msg += state.last_failed_reason
    elif state.listen_mode == "queqiao":
        msg += "鹊桥监听中"
    elif state.listen_mode == "dynamicmap":
        if not state.has_success_query:
            msg += "等待首次卫星地图状态更新"
        else:
            msg += f"服务器时间: {gametick2time(state.time)}"
            if state.thundering:
                msg += " ⛈"
            elif state.storming:
                msg += " 🌧"
            msg += "\n"
            msg += f"在线玩家数: {len(state.players)}\n"
            for player in state.players.values():
                msg += f'<{player["name"]}>\n'
                msg += f'{player["world"]}({player["x"]:.1f},{player["y"]:.1f},{player["z"]:.1f})\n'
                msg += f'HP:{player["health"]:.1f} Armor:{player["armor"]:.1f}\n'

    return msg.strip()


info = CmdHandler(["/info"], logger)
info.check_wblist(gwl).check_cdrate(cd).check_group()


@info.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    return await ctx.asend_reply_msg(build_info_message(state))


listen = CmdHandler(["/listen"], logger)
listen.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@listen.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    pre_mode = state.listen_mode
    args = ctx.get_args().strip()
    if not args:
        return await ctx.asend_reply_msg(f"当前监听模式为 {pre_mode}")

    assert_and_reply(args in LISTEN_MODES, "监听模式只能为 dynamicmap/queqiao/off")
    if args == pre_mode:
        return await ctx.asend_reply_msg(f"当前监听模式已经为 {pre_mode}")

    state.listen_mode = args
    state.failed_count = 0
    state.last_failed_reason = None
    state.failed_time = None
    state.next_query_ts = 0
    state.save()
    return await ctx.asend_reply_msg(f"修改监听模式： {pre_mode} -> {args}")


set_url = CmdHandler(["/seturl"], logger)
set_url.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@set_url.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    url = ctx.get_args().strip()
    assert_and_reply(url, "请输入正确的URL")
    if not url.startswith("http"):
        url = "http://" + url
    state.url = url
    state.next_query_ts = 0
    state.save()
    return await ctx.asend_reply_msg(f"设置MC服务器监听地址为: {url}")


get_url = CmdHandler(["/geturl"], logger)
get_url.check_wblist(gwl).check_cdrate(cd).check_group()


@get_url.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    return await ctx.asend_reply_msg(f"本群设置的MC服务器监听地址为: {state.url}")


set_info = CmdHandler(["/setinfo"], logger)
set_info.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@set_info.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    info_text = ctx.get_args().strip()
    state.info = info_text
    state.save()
    return await ctx.asend_reply_msg(f"设置MC服务器信息为: {info_text}")


set_chatprefix = CmdHandler(["/setchatprefix"], logger)
set_chatprefix.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@set_chatprefix.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    chatprefix = ctx.get_args()
    state.chatprefix = chatprefix
    state.save()
    return await ctx.asend_reply_msg(f"设置聊天前缀为: {chatprefix}")


get_chatprefix = CmdHandler(["/getchatprefix"], logger)
get_chatprefix.check_wblist(gwl).check_cdrate(cd).check_group()


@get_chatprefix.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    return await ctx.asend_reply_msg(f"本群设置的MC服务器聊天前缀为: {state.chatprefix}")


notify_on = CmdHandler(["/connect notify on"], logger)
notify_on.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@notify_on.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    state.notify_on = True
    state.save()
    return await ctx.asend_reply_msg("开启服务器断线连线通知")


notify_off = CmdHandler(["/connect notify off"], logger)
notify_off.check_wblist(gwl).check_cdrate(cd).check_group().check_superuser()


@notify_off.handle()
async def _(ctx: HandlerContext):
    state = get_group_state(ctx.group_id)
    state.notify_on = False
    state.save()
    return await ctx.asend_reply_msg("关闭服务器断线连线通知")

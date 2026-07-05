from typing import Any


SUPPORTED_GROUP_ADAPTERS = {"onebot", "qq"}
SUPPORTED_GUILD_ADAPTERS = {"qq"}
LISTEN_MODES = {"off", "dynamicmap", "queqiao"}
LEGACY_LISTEN_MODE_ALIASES = {"log": "queqiao"}
DEFAULT_CHATPREFIX = "[Server] "

# 这里保持为纯函数模块，方便在不启动 NoneBot 的情况下测试配置转换。


def _as_str(value: Any, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} 不能为空")
    value = str(value).strip()
    if not value:
        raise ValueError(f"{field_name} 不能为空")
    return value


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (set, tuple)):
        return list(value)
    return [value]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def normalize_listen_mode(value: Any, field_name: str = "listen_mode") -> str:
    # 旧配置里的 log 代表旧日志监听；mcqq 版统一映射为 queqiao。
    value = str(value or "off").strip()
    value = LEGACY_LISTEN_MODE_ALIASES.get(value, value)
    if value not in LISTEN_MODES:
        raise ValueError(f"{field_name} 只能为 off/dynamicmap/queqiao")
    return value


def should_sync_messages(listen_mode: Any) -> bool:
    # 只有 queqiao 模式承担 mcqq 双向互通，off/dynamicmap 都不转发聊天和事件。
    return normalize_listen_mode(listen_mode) == "queqiao"


def _listen_mode(value: Any, field_name: str) -> str:
    return normalize_listen_mode(value, field_name)


def target_key(adapter: str, target_id: Any) -> str:
    return f"{adapter}:{target_id}"


def _build_group(raw_group: dict[str, Any], server_name: str, index: int) -> dict[str, str]:
    if not isinstance(raw_group, dict):
        raise ValueError(f"servers.{server_name}.groups[{index}] 必须是对象")

    adapter = _as_str(raw_group.get("adapter", "onebot"), f"servers.{server_name}.groups[{index}].adapter")
    if adapter not in SUPPORTED_GROUP_ADAPTERS:
        raise ValueError(f"servers.{server_name}.groups[{index}].adapter 只支持 onebot/qq")

    return {
        "group_id": _as_str(raw_group.get("group_id"), f"servers.{server_name}.groups[{index}].group_id"),
        "adapter": adapter,
        "bot_id": _as_str(raw_group.get("bot_id"), f"servers.{server_name}.groups[{index}].bot_id"),
    }


def _build_guild(raw_guild: dict[str, Any], server_name: str, index: int) -> dict[str, str]:
    if not isinstance(raw_guild, dict):
        raise ValueError(f"servers.{server_name}.guilds[{index}] 必须是对象")

    adapter = _as_str(raw_guild.get("adapter", "qq"), f"servers.{server_name}.guilds[{index}].adapter")
    if adapter not in SUPPORTED_GUILD_ADAPTERS:
        raise ValueError(f"servers.{server_name}.guilds[{index}].adapter 只支持 qq")

    return {
        "channel_id": _as_str(raw_guild.get("channel_id"), f"servers.{server_name}.guilds[{index}].channel_id"),
        "adapter": adapter,
        "bot_id": _as_str(raw_guild.get("bot_id"), f"servers.{server_name}.guilds[{index}].bot_id"),
    }


def _servers(raw_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise ValueError("mc 配置必须是对象")
    servers = raw_config.get("servers", {})
    if not isinstance(servers, dict):
        raise ValueError("servers 必须是对象")
    if raw_config.get("enabled", True) and not servers:
        raise ValueError("启用 mc 时必须至少配置一个 servers 条目")
    return servers


def build_mc_driver_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    # nonebot-plugin-mcqq 只读取 driver config；这里把 lunabot YAML 转成它的配置形状。
    servers = _servers(raw_config)

    server_dict: dict[str, dict[str, Any]] = {}
    for server_name, raw_server in servers.items():
        server_name = _as_str(server_name, "servers.<name>")
        if not isinstance(raw_server, dict):
            raise ValueError(f"servers.{server_name} 必须是对象")

        groups = [
            _build_group(group, server_name, index)
            for index, group in enumerate(_as_list(raw_server.get("groups")))
        ]
        guilds = [
            _build_guild(guild, server_name, index)
            for index, guild in enumerate(_as_list(raw_server.get("guilds")))
        ]
        if not groups and not guilds:
            raise ValueError(f"servers.{server_name} 至少需要配置一个 groups 或 guilds 绑定")

        server_dict[server_name] = {
            "group_list": groups,
            "guild_list": guilds,
            "rcon_msg": _as_bool(raw_server.get("rcon_msg"), False),
        }

    minecraft = raw_config.get("minecraft", {}) or {}
    if not isinstance(minecraft, dict):
        raise ValueError("minecraft 必须是对象")

    mc_qq = {
        "server_dict": server_dict,
        # 不暴露/注册官方命令头，避免 /mcc、/mcst、/mcsa 出现在新 mc 插件里。
        "command_header": [],
        "ignore_message_header": raw_config.get("ignore_message_header", ["/", "."]),
        "command_priority": int(raw_config.get("command_priority", 98)),
        "command_block": bool(raw_config.get("command_block", True)),
        "notice_connected": False,
        "rcon_result_to_image": False,
        "send_group_name": bool(raw_config.get("send_group_name", False)),
        "display_server_name": bool(raw_config.get("display_server_name", True)),
        "say_way": str(raw_config.get("say_way", "：")),
        "chat_image_enable": bool(raw_config.get("chat_image_enable", False)),
        "cmd_whitelist": [],
    }

    return {
        "mc_qq": mc_qq,
        "minecraft_ws_urls": minecraft.get("ws_urls", {}),
        "minecraft_access_token": minecraft.get("access_token") or None,
    }


def build_group_defaults(raw_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # 群级状态默认值来自 YAML；运行时命令写入 data/mc/db.json 后会覆盖这些默认值。
    ret: dict[str, dict[str, Any]] = {}
    for server_name, raw_server in _servers(raw_config).items():
        server_name = _as_str(server_name, "servers.<name>")
        if not isinstance(raw_server, dict):
            raise ValueError(f"servers.{server_name} 必须是对象")

        for index, raw_group in enumerate(_as_list(raw_server.get("groups"))):
            group = _build_group(raw_group, server_name, index)
            key = target_key(group["adapter"], group["group_id"])
            if key in ret:
                raise ValueError(f"群/频道绑定重复: {key}")
            ret[key] = {
                "server_name": server_name,
                "group_id": group["group_id"],
                "adapter": group["adapter"],
                "bot_id": group["bot_id"],
                "listen_mode": _listen_mode(raw_group.get("listen_mode", "off"), f"servers.{server_name}.groups[{index}].listen_mode"),
                "url": str(raw_group.get("url", "") or ""),
                "info": str(raw_group.get("info", "") or ""),
                "chatprefix": str(raw_group.get("chatprefix", DEFAULT_CHATPREFIX)),
                "notify_on": _as_bool(raw_group.get("notify_on"), True),
            }
    return ret


def build_guild_defaults(raw_config: dict[str, Any]) -> list[dict[str, Any]]:
    ret: list[dict[str, Any]] = []
    for server_name, raw_server in _servers(raw_config).items():
        server_name = _as_str(server_name, "servers.<name>")
        if not isinstance(raw_server, dict):
            raise ValueError(f"servers.{server_name} 必须是对象")

        for index, raw_guild in enumerate(_as_list(raw_server.get("guilds"))):
            guild = _build_guild(raw_guild, server_name, index)
            ret.append({
                "server_name": server_name,
                "channel_id": guild["channel_id"],
                "adapter": guild["adapter"],
                "bot_id": guild["bot_id"],
            })
    return ret


def get_configured_targets(raw_config: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    group_ids = set()
    group_openids = set()
    channel_ids = set()

    for default in build_group_defaults(raw_config).values():
        if default["adapter"] == "onebot":
            group_ids.add(str(default["group_id"]))
        elif default["adapter"] == "qq":
            group_openids.add(str(default["group_id"]))

    for default in build_guild_defaults(raw_config):
        channel_ids.add(str(default["channel_id"]))

    return group_ids, group_openids, channel_ids


def should_ignore_message_header(
    raw_config: dict[str, Any],
    text: str,
    group_id: Any = None,
    group_openid: Any = None,
    channel_id: Any = None,
) -> bool:
    # 只过滤已绑定目标里的命令式消息，避免 /help 之类 lunabot 指令同步进 Minecraft。
    ignore_headers = tuple(
        str(header)
        for header in raw_config.get("ignore_message_header", ["/", "."])
        if str(header)
    )
    if not ignore_headers:
        return False

    group_ids, group_openids, channel_ids = get_configured_targets(raw_config)
    is_target = (
        str(group_id) in group_ids
        or str(group_openid) in group_openids
        or str(channel_id) in channel_ids
    )
    if not is_target:
        return False

    return text.lstrip().startswith(ignore_headers)


def apply_chatprefix(text: str, chatprefix: str) -> str:
    # MC -> QQ 的群内前缀由每个群单独配置。
    return f"{chatprefix or ''}{text}"

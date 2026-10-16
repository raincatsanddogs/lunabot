"""LunaBot Autochat Dashboard NoneBot2 Plugin Bridge.

Listens for `/autochat panel` and `/panel` commands, verifies admin/superuser
permissions, and issues single-use OTT ticket login URLs.
"""
from __future__ import annotations

import logging
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from ..utils import (
    CmdHandler,
    HandlerContext,
    check_superuser,
    get_logger,
    is_group_msg,
)
from src.services.autochat_dashboard.bridge import AutoChatBridge

logger = get_logger("Chat.AutochatDashboard")

# Register commands
panel_handler = CmdHandler(["/autochat panel", "/panel"], logger, use_seg_cmd=False)

driver = get_driver()


@driver.on_startup
async def _init_dashboard_bridge():
    """Idempotently bootstrap database tables and start async workers on bot startup."""
    try:
        AutoChatBridge.bootstrap()
        AutoChatBridge.start_background()
        logger.info("Autochat dashboard bridge initialized successfully on bot startup.")
    except Exception as exc:
        logger.error(f"Failed to initialize autochat dashboard bridge: {exc}", exc_info=True)


@driver.on_shutdown
async def _shutdown_dashboard_bridge():
    """Stop async workers gracefully on bot shutdown."""
    try:
        await AutoChatBridge.stop_background()
        logger.info("Autochat dashboard bridge background workers stopped.")
    except Exception as exc:
        logger.warning(f"Error shutting down autochat dashboard bridge: {exc}")


async def _get_admin_groups_for_user(bot: Bot, user_id: int) -> list[str]:
    """Find all groups where the user is an owner or admin."""
    admin_groups = []
    try:
        group_list = await bot.call_api("get_group_list")
        for g in group_list:
            gid = g.get("group_id")
            if not gid:
                continue
            try:
                member_info = await bot.call_api(
                    "get_group_member_info", group_id=gid, user_id=user_id, no_cache=True
                )
                if member_info.get("role") in ("owner", "admin"):
                    admin_groups.append(str(gid))
            except Exception:
                continue
    except Exception as exc:
        logger.warning(f"Failed to query group list for user {user_id}: {exc}")
    return admin_groups


@panel_handler.handle()
async def handle_dashboard_panel(ctx: HandlerContext):
    """Handle /autochat panel command."""
    user_id = ctx.user_id
    is_group = is_group_msg(ctx.event)
    is_super = check_superuser(ctx.event)

    allowed_groups = []
    allowed_scopes = []
    role = "group_admin"

    if is_super:
        role = "superuser"
        allowed_groups = ["*"]
        allowed_scopes = ["*"]
    elif is_group:
        # Check current group permission
        try:
            member = await ctx.bot.call_api(
                "get_group_member_info", group_id=ctx.group_id, user_id=user_id, no_cache=True
            )
            if member.get("role") not in ("owner", "admin"):
                return await ctx.asend_reply_msg("访问看板需要本群群主、管理员或超级管理权限。")
            allowed_groups = [str(ctx.group_id)]
            allowed_scopes = [f"{ctx.bot.self_id}:{ctx.group_id}"]
        except Exception:
            return await ctx.asend_reply_msg("身份权限校验失败，请稍后重试。")
    else:
        # Private message from non-superuser: search all managed groups
        admin_groups = await _get_admin_groups_for_user(ctx.bot, user_id)
        if not admin_groups:
            return await ctx.asend_reply_msg("您当前在任何群中均非管理员，无权访问管理看板。")
        allowed_groups = admin_groups
        allowed_scopes = [f"{ctx.bot.self_id}:{gid}" for gid in admin_groups]

    # Generate one-time OTT ticket link
    try:
        from src.services.autochat_dashboard.server.config import settings

        base_url = (settings.PUBLIC_URL or f"http://127.0.0.1:{settings.PORT}").rstrip("/")
        ticket_url = AutoChatBridge.issue_ticket(
            user_id=str(user_id),
            role=role,
            allowed_groups=allowed_groups,
            allowed_scopes=allowed_scopes,
            base_url=base_url,
        )

        reply_text = (
            f"🌙 LunaBot Autochat 管理看板专属登录链接（180秒内有效，点击即焚）：\n"
            f"{ticket_url}\n\n"
            f"请勿转发给他人。链接已自动挂载一次性凭据，浏览器打开即可免密登录。"
        )

        if is_group:
            # Send via private message to prevent ticket leaking in group
            try:
                await ctx.bot.send_private_msg(user_id=user_id, message=reply_text)
                return await ctx.asend_reply_msg(
                    "已将一次性免密登录链接私信发送给您，请查收（为防泄露请勿群内传播）。"
                )
            except Exception:
                # If private message blocked by privacy settings, fallback to group reply
                return await ctx.asend_reply_msg(reply_text)
        else:
            return await ctx.asend_reply_msg(reply_text)
    except Exception as exc:
        logger.error(f"Failed to issue panel ticket: {exc}", exc_info=True)
        return await ctx.asend_reply_msg("签发看板登录链接失败，请检查控制台日志。")

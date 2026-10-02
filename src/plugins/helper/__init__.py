from ..utils import *
from .docs import parse_help_document
from .render import HelpRenderer, IndexOptions, PageOutOfRange, page_text

config = Config('helper')
logger = get_logger('Helper')
file_db = get_file_db('data/helper/db.json', logger)
gbl = get_group_black_list(file_db, logger, 'helper')
cd = ColdDown(file_db, logger)


HELP_DOCS_DIR = Path("helps")
help_renderer = HelpRenderer("data/helper/index_cache", PlaywrightPage, logger.print_exc)

def _get_prefix() -> str:
    try:
        return get_command_prefix()
    except (NameError, Exception):
        return "/"


help = CmdHandler(['/help', '/帮助'], logger, block=True)
help.check_wblist(gbl).check_cdrate(cd)
@help.handle()
async def handle_help(ctx: HandlerContext):
    args = ctx.get_args().strip()
    parts = args.split(maxsplit=1)
    service = parts[0].casefold() if parts else ""
    query = parts[1].strip() if len(parts) == 2 else ""
    documents = {}
    for path in sorted(HELP_DOCS_DIR.glob("*.md")):
        if path.stem == "main":
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                title = stream.readline().lstrip("# ").strip()
            documents[path.stem] = (path, title)
        except Exception:
            logger.print_exc(f"读取帮助目录 {path} 失败")

    doc_url = (config.get('doc_url') or "").strip()

    if service not in documents:
        if not service and doc_url:
            template: str = config.get('doc_template') or ""
            if not template:
                template = (
                    "【LunaBot 使用帮助】\n"
                    "完整使用指南与指令手册请访问网页文档站：\n"
                    "{doc_url}\n\n"
                    "发送 /help 英文服务名 查看各服务的指令索引（例如 /help sekai）\n"
                    "发送 /help 服务名 指令名 或在指令后追加 help 查看指令详情（例如 /help sekai 查卡 或 /查卡 help）"
                )
            if r"{doc_url}" in template:
                content = template.format(doc_url=doc_url).strip()
            else:
                content = template.strip()
            return await ctx.asend_fold_msg_adaptive(content, need_reply=False)

        if service and doc_url:
            content = f"未找到服务 {service}。\n完整指令手册与可用服务请访问网页文档站：\n{doc_url}\n\n发送 /help 查看可用服务或帮助引导"
            return await ctx.asend_fold_msg_adaptive(content, need_reply=False)

        service_list_text = "\n".join(f"{name} - {title}" for name, (_, title) in documents.items())
        template: str = config.get('template') or ""
        if r"{service_list}" in template:
            template = template.format(service_list=service_list_text.strip())
        if service:
            template = f"未找到服务 {service}，可用服务如下：\n\n{template}"
        return await ctx.asend_fold_msg_adaptive(template.strip(), need_reply=False)

    try:
        doc_path = documents[service][0]
        document = parse_help_document(service, doc_path.read_text(encoding="utf-8"))
        if not document.entries:
            return await ctx.asend_reply_msg(f"{service} 暂无可用的指令帮助。")
    except Exception:
        logger.print_exc(f"读取 {service} 帮助文档失败")
        return await ctx.asend_reply_msg("帮助文档读取失败，请稍后再试。")

    try:
        options = IndexOptions.from_config(config.get("index", {}))
    except (ValueError, TypeError, AttributeError):
        logger.print_exc("帮助索引配置无效，使用默认分页参数")
        options = IndexOptions()

    pfx = _get_prefix()

    if not query or re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", query):
        try:
            number = int(query) if query else 1
        except ValueError:
            number = 0
        try:
            page = await help_renderer.get_index(document, number, options, prefix=pfx)
        except PageOutOfRange as exc:
            return await ctx.asend_reply_msg(f"{exc}。例如：{pfx}help {service} 1")
        if page.image_path:
            try:
                message = await get_image_cq(str(page.image_path), low_quality=False)
            except Exception:
                logger.print_exc(f"编码 {service} 指令索引失败，回退文字")
            else:
                return await ctx.asend_reply_msg(message)
        return await ctx.asend_reply_msg(page_text(document, page, prefix=pfx))

    matches = document.find(query, prefix=pfx)
    if not matches:
        sample_cmd = document.entries[0].primary_with_prefix(pfx).removeprefix(pfx) if document.entries else "指令名"
        return await ctx.asend_reply_msg(
            f"未找到 {service} 的指令“{query}”。请使用完整指令名、别名或小节标题。\n"
            f"发送 {pfx}help {service} 查看索引；例如 {pfx}help {service} {sample_cmd}"
        )
    if len(matches) > 1:
        candidates = "\n".join(f"{pfx}help {service} {entry.title} — {entry.primary_with_prefix(pfx)}" for entry in matches)
        return await ctx.asend_reply_msg(f"匹配到多个帮助条目，请使用小节标题查询：\n{candidates}")
    entry = matches[0]
    try:
        path = await help_renderer.get_detail(document, entry, options, prefix=pfx)
        message = await get_image_cq(str(path), low_quality=False)
    except Exception:
        logger.print_exc(f"渲染 {service} / {entry.title} 帮助详情失败，回退文字")
        return await ctx.asend_fold_msg_adaptive(entry.detail_markdown(service, prefix=pfx), fallback_method="seperate")
    return await ctx.asend_reply_msg(message)

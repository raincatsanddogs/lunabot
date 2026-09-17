from ..record import before_record_hook
from ..utils import *
from ..utils.rpc import *
from ..llm import ChatSession, get_text_embedding, describe_embedding_model
from ..llm.api_provider_manager import api_provider_mgr
from src.services.autochat.store import Store
from src.services.autochat.types import Event as AutochatEvent, Scope, PROTOCOL_VERSION
from src.services.autochat.websearch import TavilyProvider
from src.services.autochat.wire import outgoing_segments
from src.services.autochat.stickers import parse_sticker_command, format_sticker_result
from src.services.autochat.management import (
    parse_command,
    requires_admin,
    operation_id,
    format_result,
    WRITES,
)

config = Config('chat.autochat')
logger = get_logger('Chat')
file_db = get_file_db('data/chat/db.json', logger)
chat_gwl = get_group_white_list(file_db, logger, 'chat')
autochat_gwl = get_group_white_list(file_db, logger, 'autochat', is_service=False)
_store = None
_sessions = {}
_engine_id = None
RPC_SERVICE = 'autochat'
_rpc_token = config.get('rpc.token')
_rpc_consumer_id = config.get('rpc.consumer_id', 'autochat')
_search_provider = None


def get_autochat_store():
    global _store
    if _store is None:
        _store = Store('data/chat/autochat/bridge')
        _store.recover_actions()
    return _store


@before_record_hook
async def record_new_message(bot: Bot, event: MessageEvent):
    if not is_group_msg(event) or str(event.user_id) == str(bot.self_id):
        return
    if not chat_gwl.check_id(event.group_id) or not autochat_gwl.check_id(event.group_id):
        return
    if event.message.extract_plain_text().strip().startswith(('/um', '/autochat um', '/autochat sticker')):
        return
    get_autochat_store().add_event(
        AutochatEvent(
            str(bot.self_id),
            str(event.group_id),
            str(event.message_id),
            str(event.user_id),
            float(event.time),
            get_msg(event),
            get_user_name_by_event(event),
        ),
        time.time(),
    )


def on_connect(session):
    _sessions[session.id] = session


def on_disconnect(session):
    global _engine_id
    _sessions.pop(session.id, None)
    if _engine_id == session.id:
        _engine_id = None


start_rpc_service(
    host=config.get('rpc.host'),
    port=config.get('rpc.port'),
    token=_rpc_token,
    name=RPC_SERVICE,
    logger=logger,
    on_connect=on_connect,
    on_disconnect=on_disconnect,
    max_message_bytes=config.get('rpc.max_message_bytes', 128 * 1024 * 1024),
)


def check_protocol(version):
    if version != PROTOCOL_VERSION:
        raise ValueError(f'Unsupported autochat protocol; expected v{PROTOCOL_VERSION}')


@rpc_method(RPC_SERVICE, 'register_engine')
async def handle_register_engine(cid, version, consumer_id):
    global _engine_id
    check_protocol(version)
    if _engine_id and _engine_id != cid and _engine_id in _sessions:
        raise ValueError('Another autochat engine is already connected')
    if consumer_id != _rpc_consumer_id:
        raise ValueError('Unexpected engine consumer_id')
    _engine_id = cid
    return {'protocol_version': PROTOCOL_VERSION}


@rpc_method(RPC_SERVICE, 'describe_models')
async def handle_describe_models(cid, version, names):
    check_protocol(version)
    result = {}
    for name in names:
        embedding = describe_embedding_model(name)
        if embedding is not None:
            result[name] = embedding
            continue
        model = api_provider_mgr.find_model(name)
        result[name] = {
            **model.provider.describe_model(model.get_model_id()),
            'multimodal': model.is_multimodal,
            'tools': model.supports_tools,
            'parallel_tools': model.supports_parallel_tools,
            'context_window': model.max_token,
        }
    return result


@rpc_method(RPC_SERVICE, 'query_llm')
async def handle_query_llm(cid, request):
    check_protocol(request['protocol_version'])
    session = ChatSession()
    session.content = request['messages']
    session.has_image = any(
        isinstance(m.get('content'), list)
        and any(p.get('type') == 'image_url' for p in m['content'])
        for m in session.content
    )
    opts = request.get('options', {})
    response = await session.get_response(
        request['model'],
        tools=request.get('tools', []),
        timeout=opts.get('timeout', 120),
        max_tokens=opts.get('max_tokens', 2048),
    )
    return {
        'assistant_message': response.assistant_message,
        'tool_calls': response.tool_calls,
        'provider_state': response.provider_state,
        'usage': response.usage,
        'finish_reason': response.finish_reason,
    }


@rpc_method(RPC_SERVICE, 'query_embedding')
async def handle_query_embedding(cid, version, texts, model):
    check_protocol(version)
    embeddings, usage = await get_text_embedding(texts, model, with_usage=True)
    return {'embeddings': embeddings, 'usage': usage}


@rpc_method(RPC_SERVICE, 'poll_events')
async def handle_poll_events(cid, version, consumer_id, after, limit):
    check_protocol(version)
    store = get_autochat_store()
    enabled = {
        s.key: chat_gwl.check_id(int(s.group_id)) and autochat_gwl.check_id(int(s.group_id))
        for s in store.scopes()
    }
    for key in store.get('managed_scopes', []):
        gid = int(key.split(':', 1)[1])
        enabled[key] = chat_gwl.check_id(gid) and autochat_gwl.check_id(gid)
    rows = store.db.execute(
        'SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?',
        (max(0, int(after)), min(100, max(1, int(limit)))),
    ).fetchall()
    return {
        'protocol_version': PROTOCOL_VERSION,
        'events': [{**loads_json(r['payload']), 'seq': r['seq']} for r in rows],
        'enabled': enabled,
    }


@rpc_method(RPC_SERVICE, 'ack_events')
async def handle_ack_events(cid, version, consumer_id, cursor):
    check_protocol(version)
    store, key = get_autochat_store(), 'consumer:' + str(consumer_id)
    store.set(key, max(store.get(key, 0), int(cursor)))
    return {'cursor': store.get(key)}


@rpc_method(RPC_SERVICE, 'send_action')
async def handle_send_action(cid, version, consumer_id, bot_id, group_id, action_id, segments):
    check_protocol(version)
    gid = int(group_id)
    if not chat_gwl.check_id(gid) or not autochat_gwl.check_id(gid):
        return {'state': 'cancelled', 'reason': 'group_disabled'}
    bot = await aget_group_bot(gid, raise_exc=True)
    if str(bot.self_id) != str(bot_id):
        return {'state': 'failed', 'reason': 'bot_scope_mismatch'}
    wire, compact = outgoing_segments(segments, config.get('chat.media.file_bytes', 20 * 1024 * 1024))
    scope, store = Scope(str(bot_id), str(group_id)), get_autochat_store()
    key = scope.key + ':' + str(consumer_id) + ':' + str(action_id)
    action = store.start_action(key, scope, {'segments': compact})
    if action['state'] != 'pending':
        return {
            'state': action['state'] if action['state'] != 'sending' else 'unknown',
            **(action['result'] or {}),
        }
    store.finish_action(key, 'sending', {})
    try:
        if action['payload']['segments'] != compact:
            raise ValueError('Action payload changed')
        response = await bot.send_group_msg(
            group_id=gid, message=Message(wire)
        )
        store.finish_action(key, 'sent', response)
        return {'state': 'sent', **response}
    except Exception:
        store.finish_action(key, 'unknown', {})
        return {'state': 'unknown'}


def get_search_provider(name):
    global _search_provider
    if name != 'tavily':
        raise ValueError('Unknown search provider')
    if _search_provider is None:
        provider_config = Config('llm.providers.tavily')
        _search_provider = TavilyProvider(provider_config.get_all)
    return _search_provider


@rpc_method(RPC_SERVICE, 'describe_search')
async def handle_describe_search(cid, version, provider):
    check_protocol(version)
    try:
        return get_search_provider(provider).describe()
    except Exception:
        return {'available': False, 'error': 'provider_not_configured'}


@rpc_method(RPC_SERVICE, 'web_tool')
async def handle_web_tool(cid, version, provider, method, arguments):
    check_protocol(version)
    try:
        return await get_search_provider(provider).execute(method, arguments)
    except Exception:
        return {'error': 'search_provider_unavailable'}


async def handle_sticker_command(ctx):
    if not is_group_msg(ctx.event):
        return await ctx.asend_reply_msg('请在需要管理表情包的群内使用此指令')
    try:
        if not await memory_permission(ctx):
            raise PermissionError('此操作需要本群群主、管理员或超级管理权限')
        command = parse_sticker_command(ctx.get_args().strip())
        session = _sessions.get(_engine_id)
        if session is None:
            return await ctx.asend_reply_msg('autochat 会话服务离线，表情包操作未提交')
        images = []
        if command['op'] == 'add':
            for segment in get_msg(ctx.event):
                if segment['type'] == 'image' and segment['data'].get('url'):
                    images.append(segment['data']['url'])
        payload = {'bot_id': str(ctx.bot.self_id), 'group_id': str(ctx.group_id),
                   'actor_id': str(ctx.user_id), 'message_id': str(ctx.message_id),
                   'admin': True, 'command': command, 'images': images}
        result = await asyncio.wait_for(session.send_request('manage_stickers', [_rpc_token, PROTOCOL_VERSION, payload]), 10)
        return await ctx.asend_fold_msg_adaptive(format_sticker_result(result))
    except (asyncio.TimeoutError, ConnectionError, OSError):
        return await ctx.asend_reply_msg('表情包操作结果待确认，请重发同一请求或查询素材列表；不会重复保存同一图片')
    except (ValueError, PermissionError, aiorpcx.RPCError) as exc:
        return await ctx.asend_reply_msg(str(exc))


async def memory_permission(ctx):
    if check_superuser(ctx.event):
        return True
    try:
        member = await ctx.bot.call_api(
            'get_group_member_info', group_id=ctx.group_id, user_id=ctx.user_id, no_cache=True
        )
        return member.get('role') in ('owner', 'admin')
    except Exception:
        return False


async def handle_memory_command(ctx):
    if not is_group_msg(ctx.event):
        return await ctx.asend_reply_msg('请在需要管理记忆的群内使用此指令')
    try:
        command = parse_command(ctx.get_args().strip(), ctx.get_at_qids(), ctx.user_id)
        admin = await memory_permission(ctx)
        if requires_admin(command) and not admin:
            return await ctx.asend_reply_msg(
                '此操作需要本群群主、管理员或超级管理权限；身份查询失败时不能编辑'
            )
        session = _sessions.get(_engine_id)
        if session is None:
            return await ctx.asend_reply_msg('autochat 会话服务离线，记忆操作未提交')
        scope = Scope(str(ctx.bot.self_id), str(ctx.group_id))
        if command['op'] in WRITES and command.get('subjects'):
            for uid in command['subjects']:
                await ctx.bot.call_api(
                    'get_group_member_info', group_id=ctx.group_id, user_id=int(uid), no_cache=True
                )
        store = get_autochat_store()
        store.set('managed_scopes', sorted(set(store.get('managed_scopes', [])) | {scope.key}))
        payload = {
            'bot_id': scope.bot_id,
            'group_id': scope.group_id,
            'actor_id': str(ctx.user_id),
            'message_id': str(ctx.message_id),
            'admin': admin,
            'command': command,
        }
        try:
            result = await asyncio.wait_for(
                session.send_request('manage_memory', [_rpc_token, PROTOCOL_VERSION, payload]), 10
            )
        except (asyncio.TimeoutError, ConnectionError, OSError):
            if command['op'] in WRITES:
                oid = operation_id(scope, ctx.user_id, ctx.message_id)
                return await ctx.asend_reply_msg(
                    f'操作结果待确认，勿重复新增。查询：/um operation {oid}'
                )
            return await ctx.asend_reply_msg('会话服务暂时无法响应，请稍后查询')
        return await ctx.asend_fold_msg_adaptive(format_result(result))
    except (ValueError, PermissionError, aiorpcx.RPCError) as exc:
        return await ctx.asend_reply_msg(str(exc))

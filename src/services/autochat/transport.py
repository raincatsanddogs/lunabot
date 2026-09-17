from __future__ import annotations

import asyncio

from src.llm_core import ModelTurn, ModelSpec
from .types import PROTOCOL_VERSION


class RpcPlatform:
    """双向认证连接：向 Luna 拉取事件，接收记忆和素材管理请求。"""

    def __init__(self, url, token, consumer_id):
        self.url, self.token, self.consumer_id = url, token, consumer_id
        self.connection = None
        self.session = None
        self.connect_lock = asyncio.Lock()
        self.management_handler = None
        self.sticker_handler = None

    async def connect(self):
        import aiorpcx

        async with self.connect_lock:
            if self.session is None:
                platform = self

                async def handle_reverse_request(request):
                    if (
                        request.method not in ('manage_memory', 'manage_stickers')
                        or len(request.args) != 3
                        or request.args[0] != platform.token
                        or request.args[1] != PROTOCOL_VERSION
                    ):
                        raise aiorpcx.RPCError(-32000, 'Unauthorized management request')
                    handler = platform.management_handler if request.method == 'manage_memory' else platform.sticker_handler
                    if handler is None:
                        raise aiorpcx.RPCError(-32001, 'Engine is not ready')
                    try:
                        return await handler(request.args[2])
                    except (ValueError, PermissionError, KeyError) as exc:
                        raise aiorpcx.RPCError(-32602, str(exc)) from exc

                self.connection = aiorpcx.connect_ws(self.url, max_size=128 * 1024 * 1024)
                self.session = await self.connection.__aenter__()
                # aiorpcx 0.25 WSClient drops its session_factory in __aenter__.
                # Bind the callback on this session before registering it.
                self.session.handle_request = handle_reverse_request
                self.session.sent_request_timeout = 130
                try:
                    await self.session.send_request(
                        'register_engine', [self.token, PROTOCOL_VERSION, self.consumer_id]
                    )
                except Exception:
                    connection, self.connection = self.connection, None
                    self.session = None
                    await connection.__aexit__(None, None, None)
                    raise

    async def close(self):
        connection, self.connection = self.connection, None
        self.session = None
        if connection:
            await connection.__aexit__(None, None, None)

    async def call(self, method, *args):
        await self.connect()
        try:
            return await self.session.send_request(method, [self.token, *args])
        except Exception:
            await self.close()
            raise

    async def poll(self, cursor):
        return await self.call("poll_events", PROTOCOL_VERSION, self.consumer_id, cursor, 100)

    async def ack(self, cursor):
        return await self.call("ack_events", PROTOCOL_VERSION, self.consumer_id, cursor)

    async def send(self, scope, segments, action_id):
        return await self.call(
            "send_action",
            PROTOCOL_VERSION,
            self.consumer_id,
            scope.bot_id,
            scope.group_id,
            action_id,
            segments,
        )

    async def describe_search(self, provider):
        return await self.call('describe_search', PROTOCOL_VERSION, provider)

    async def search(self, provider, method, arguments):
        return await self.call('web_tool', PROTOCOL_VERSION, provider, method, arguments)


class RpcGateway:
    """生产模型经 Luna 调用，沿用供应商客户端、能力声明和额度管理。"""

    def __init__(self, platform):
        self.platform = platform
        self.models = {}

    async def describe(self, models):
        value = await self.platform.call(
            'describe_models', PROTOCOL_VERSION, list(dict.fromkeys(m for m in models if m))
        )
        self.models.update({name: ModelSpec(**spec) for name, spec in value.items()})

    async def query_llm(self, model, messages, tools=None, options=None):
        value = await self.platform.call(
            "query_llm",
            {
                "protocol_version": PROTOCOL_VERSION,
                "model": model,
                "messages": messages,
                "tools": tools or [],
                "options": options or {},
            },
        )
        return ModelTurn(**value)

    async def embed(self, model, texts):
        value = await self.platform.call('query_embedding', PROTOCOL_VERSION, texts, model)
        return value['embeddings'], value['usage']

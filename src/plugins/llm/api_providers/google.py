"""将 Luna 的供应商客户端接口适配为 Gemini 原生非流式调用。"""

import copy
from types import SimpleNamespace

from ...llm.api_provider import ApiProvider
from src.llm_core import Gateway, ModelSpec


class GenaiCompletions:
    def __init__(self, api_key, http_options):
        self.api_key = api_key
        self.http_options = http_options

    def gateway(self, model):
        # 底层协议序列化与独立模拟器共用，供应商配置只在此处解释。
        specification = ModelSpec(
            name=model,
            protocol='gemini',
            api_key=self.api_key,
            base_url=self.http_options.get('base_url')
            or 'https://generativelanguage.googleapis.com',
            auth_header=self.http_options.get('auth_header', ''),
            auth_scheme=self.http_options.get('auth_scheme'),
            api_version=self.http_options.get('api_version') or 'v1beta',
            multimodal=True,
            tools=True,
            parallel_tools=True,
        )
        return Gateway({'current': specification})

    async def create(
        self,
        model,
        messages,
        extra_body=None,
        max_tokens=None,
        thinking_config=None,
        tools=None,
        parallel_tool_calls=None,
    ):
        extra = copy.deepcopy(extra_body or {})
        image_response = extra.pop('image_response', False)
        extra.pop('modalities', None)
        generation = extra.setdefault('generationConfig', {})
        generation.setdefault(
            'responseModalities', ['IMAGE', 'TEXT'] if image_response else ['TEXT']
        )

        # ModelTurn 同时携带可显示内容和原生响应块。不要先转成纯文本，
        # 否则下一轮工具结果会丢失 functionCall ID 和 thought signature。
        return await self.gateway(model).query_llm(
            'current',
            messages,
            tools,
            {
                'max_tokens': max_tokens,
                'extra': extra,
                'thinking_config': thinking_config,
                'proxy': self.http_options.get('proxy'),
                'timeout': self.http_options.get('timeout', 300),
            },
        )


class GenaiEmbeddings(GenaiCompletions):
    async def create(self, input, model, encoding_format='float'):
        inputs = [input] if isinstance(input, str) else input
        embeddings, usage = await self.gateway(model).embed('current', inputs, self.http_options)
        # 对外返回与其他供应商相同的 embedding 结构；缺少 usage 保持未知。
        return {
            'data': [
                {'index': index, 'embedding': vector} for index, vector in enumerate(embeddings)
            ],
            'usage': {**usage, 'prompt_tokens': usage.get('promptTokenCount')},
        }


class GenaiAsyncClient:
    def __init__(self, http_options, api_key):
        self.chat = SimpleNamespace(completions=GenaiCompletions(api_key, http_options))
        self.embeddings = GenaiEmbeddings(api_key, http_options)


class GoogleApiProvider(ApiProvider):
    def __init__(self):
        super().__init__(name='google', code='gg')

    def get_client(self):
        options = {
            **self.config.get('http_options', {}),
            'auth_header': self.config.get('auth_header', ''),
            'auth_scheme': self.config.get('auth_scheme', None),
        }
        return GenaiAsyncClient(http_options=options, api_key=self.get_api_key())

    def describe_model(self, model_id):
        options = self.config.get('http_options', {})
        return {
            'name': model_id,
            'protocol': 'gemini',
            'base_url': options.get('base_url') or 'https://generativelanguage.googleapis.com',
            'api_version': options.get('api_version') or 'v1beta',
        }

    def prepare_messages(self, messages):
        # 原生响应块由 Gemini 序列化器原样续传，不能用 OpenAI 的字段过滤。
        return copy.deepcopy(messages)

    async def sync_quota(self):
        return None

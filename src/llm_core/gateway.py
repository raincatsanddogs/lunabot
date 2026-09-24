from __future__ import annotations

import copy
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field


@dataclass
class ModelSpec:
    name: str
    protocol: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    auth_header: str = ""
    auth_scheme: str | None = None
    multimodal: bool = False
    tools: bool = False
    parallel_tools: bool = False
    context_window: int = 32768
    api_version: str = 'v1beta'
    embedding_dimension: int | None = None
    query_instruction: str = ''
    extra: dict = field(default_factory=dict)

    def key(self):
        return self.api_key or os.environ.get(self.api_key_env, "")

    def headers(self):
        header = self.auth_header or (
            'x-goog-api-key' if self.protocol == 'gemini' else 'Authorization'
        )
        scheme = (
            self.auth_scheme
            if self.auth_scheme is not None
            else ('Bearer' if header.lower() == 'authorization' else '')
        )
        return {header: f'{scheme} {self.key()}' if scheme else self.key()}


@dataclass
class ModelTurn:
    assistant_message: dict
    tool_calls: list[dict]
    provider_state: dict
    usage: dict
    finish_reason: str | None

    def to_dict(self):
        return asdict(self)


def normalize_openai(raw: dict) -> ModelTurn:
    choice = raw["choices"][0]
    message = copy.deepcopy(choice["message"])
    message.setdefault("role", "assistant")
    usage = raw.get("usage") or {}
    return ModelTurn(
        message,
        message.get("tool_calls") or [],
        {},
        {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "raw": usage,
        },
        choice.get("finish_reason"),
    )


def gemini_payload(model: str, messages: list[dict], tools: list[dict], options: dict) -> dict:
    contents, systems = [], []
    names, native_ids = {}, {}
    for message in messages:
        role = message["role"]
        content = message.get("content")
        if role in ("system", "developer", "system_prompt"):
            systems.append({"text": str(content or "")})
            continue
        for call in message.get("tool_calls", []):
            names[call["id"]] = call["function"]["name"]
        state = message.get("provider_state") or {}
        if role == "assistant" and state.get("protocol") == "gemini":
            native_calls = [
                p['functionCall'] for p in state['content'].get('parts', []) if 'functionCall' in p
            ]
            for call, native in zip(message.get('tool_calls', []), native_calls):
                if native.get('id'):
                    native_ids[call['id']] = native['id']
            contents.append(copy.deepcopy(state["content"]))
            continue
        parts = []
        if role == "tool":
            try:
                value = json.loads(content)
            except (TypeError, ValueError):
                value = {"result": content}
            call_id = message["tool_call_id"]
            parts.append(
                {
                    "functionResponse": {
                        "name": names.get(call_id, message.get("name", "unknown")),
                        "response": value if isinstance(value, dict) else {"result": value},
                    }
                }
            )
            if call_id in native_ids:
                parts[-1]['functionResponse']['id'] = native_ids[call_id]
        else:
            for part in (
                [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            ):
                if part.get("type") == "text" and part.get("text"):
                    parts.append({"text": part["text"]})
                elif part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:"):
                        raise ValueError("Gemini images must be materialized before serialization")
                    header, data = url.split(",", 1)
                    parts.append(
                        {"inlineData": {"mimeType": header[5:].split(";")[0], "data": data}}
                    )
            for call in message.get("tool_calls") or []:
                args = call["function"]["arguments"]
                parts.append(
                    {
                        "functionCall": {
                            "name": call["function"]["name"],
                            "args": json.loads(args) if isinstance(args, str) else args,
                        }
                    }
                )
        if parts:
            api_role = "model" if role == "assistant" else "user"
            if (
                role == "tool"
                and contents
                and contents[-1]["role"] == "user"
                and all("functionResponse" in p for p in contents[-1]["parts"])
            ):
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": api_role, "parts": parts})
    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": options.get("max_tokens", 2048)},
    }
    if systems:
        payload["systemInstruction"] = {"parts": systems}
    if tools:
        declarations = []
        for tool in tools:
            fn = tool["function"]
            declaration = {k: copy.deepcopy(fn[k]) for k in ("name", "description") if k in fn}
            if 'parameters' in fn:
                declaration['parametersJsonSchema'] = copy.deepcopy(fn['parameters'])
            declarations.append(declaration)
        payload["tools"] = [{"functionDeclarations": declarations}]
    extra = copy.deepcopy(options.get('extra', {}))
    payload['generationConfig'].update(extra.pop('generationConfig', {}))
    payload.update(extra)
    if options.get("thinking_config"):
        payload["generationConfig"]["thinkingConfig"] = options["thinking_config"]
    thinking = payload['generationConfig'].get('thinkingConfig', {})
    for snake, camel in [
        ('thinking_budget', 'thinkingBudget'),
        ('include_thoughts', 'includeThoughts'),
        ('thinking_level', 'thinkingLevel'),
    ]:
        if snake in thinking:
            thinking[camel] = thinking.pop(snake)
    return payload


def normalize_gemini(raw: dict) -> ModelTurn:
    if not raw.get("candidates"):
        raise ValueError("Gemini returned no candidates")
    candidate = raw["candidates"][0]
    native = copy.deepcopy(candidate.get("content", {"role": "model", "parts": []}))
    native.setdefault("role", "model")
    texts, calls, images = [], [], []
    digest = raw.get('responseId') or uuid.uuid4().hex[:16]
    for index, part in enumerate(native.get("parts", [])):
        if "functionCall" in part:
            call = part["functionCall"]
            calls.append(
                {
                    "id": call.get("id") or f"gemini_{digest}_{index}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("args", {}), ensure_ascii=False),
                    },
                }
            )
        elif "text" in part and not part.get("thought"):
            texts.append(part["text"])
        elif "inlineData" in part:
            data = part["inlineData"]
            images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{data['mimeType']};base64,{data['data']}"},
                }
            )
    state = {"protocol": "gemini", "content": native}
    message = {"role": "assistant", "content": "".join(texts) or None, "provider_state": state}
    if calls:
        message["tool_calls"] = calls
    if images:
        message["images"] = images
    thoughts = ''.join(
        p['text'] for p in native.get('parts', []) if p.get('thought') and 'text' in p
    )
    if thoughts:
        message['reasoning_content'] = thoughts
    usage = raw.get("usageMetadata", {})
    return ModelTurn(
        message,
        calls,
        state,
        {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "cached_tokens": usage.get("cachedContentTokenCount"),
            "reasoning_tokens": usage.get("thoughtsTokenCount"),
            "raw": usage,
        },
        candidate.get("finishReason"),
    )


def openai_messages(messages: list[dict]) -> list[dict]:
    """清洗并规范化 OpenAI 兼容接口的上下文消息列表：
    1. 剔除无效空字段（如 refusal, audio, annotations 等为 None 的属性）与内部状态（provider_state）。
    2. 确保 assistant.tool_calls 与 tool 响应严格配对且紧跟其后。
    3. 合并或规整连续的同角色消息，防止角色交替错位触发上游 Google/New-API 400 错误。
    """
    if not messages:
        return []

    cleaned = []
    allowed_keys = {
        'role',
        'content',
        'name',
        'tool_calls',
        'tool_call_id',
        'reasoning_content',
    }
    for m in messages:
        if not isinstance(m, dict):
            continue
        item = {}
        for k, v in m.items():
            if k not in allowed_keys:
                continue
            if v is None and k in ('refusal', 'audio', 'annotations', 'function_call'):
                continue
            item[k] = copy.deepcopy(v)
        if 'role' in item:
            cleaned.append(item)

    result = []
    for msg in cleaned:
        role = msg['role']
        if role == 'tool':
            call_id = msg.get('tool_call_id')
            if not call_id:
                continue
            prev_assistant = next(
                (m for m in reversed(result) if m.get('role') == 'assistant'), None
            )
            if not prev_assistant or not any(
                c.get('id') == call_id for c in prev_assistant.get('tool_calls', [])
            ):
                continue
            result.append(msg)
        elif role == 'assistant':
            if result and result[-1].get('role') == 'assistant':
                prev = result[-1]
                if msg.get('tool_calls') and not prev.get('tool_calls'):
                    result[-1] = msg
                elif not msg.get('tool_calls') and prev.get('tool_calls'):
                    pass
                elif msg.get('tool_calls') and prev.get('tool_calls'):
                    if prev.get('tool_calls') == msg.get('tool_calls'):
                        pass
                    else:
                        result.append(msg)
                else:
                    c1 = prev.get('content') or ''
                    c2 = msg.get('content') or ''
                    prev['content'] = f'{c1}\n{c2}'.strip()
            else:
                result.append(msg)
        elif role == 'user':
            result.append(msg)
        else:
            result.append(msg)

    final_messages = []
    i = 0
    while i < len(result):
        curr = result[i]
        final_messages.append(curr)
        if curr.get('role') == 'assistant' and curr.get('tool_calls'):
            expected_ids = [c['id'] for c in curr['tool_calls'] if c.get('id')]
            tool_msgs = []
            j = i + 1
            while j < len(result) and result[j].get('role') == 'tool':
                tool_msgs.append(result[j])
                j += 1
            received_ids = {m.get('tool_call_id') for m in tool_msgs}
            for tid in expected_ids:
                if tid not in received_ids:
                    tool_msgs.append(
                        {
                            'role': 'tool',
                            'tool_call_id': tid,
                            'content': '{"error": "tool_execution_skipped"}',
                        }
                    )
            final_messages.extend(tool_msgs)
            i = j
            continue
        i += 1

    return final_messages


def validate_embeddings(vectors, count, dimension=None):
    """共享向量校验，保证 Luna 客户端和独立服务使用相同的校验规则。"""
    dimensions = {len(vector) for vector in vectors if isinstance(vector, list)}
    if (
        len(vectors) != count
        or len(dimensions) != 1
        or 0 in dimensions
        or any(not isinstance(vector, list) for vector in vectors)
    ):
        raise ValueError('Invalid embedding count or dimensions')
    if dimension is not None and dimensions != {dimension}:
        raise ValueError('Embedding dimension differs from configuration')
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for vector in vectors
        for value in vector
    ):
        raise ValueError('Invalid embedding values')


def normalize_openai_embeddings(raw, count, dimension=None):
    """按响应索引恢复输入顺序；缺少 usage 时保持空值，不估算为零。"""
    if not isinstance(raw.get('data'), list) or any(
        type(item.get('index')) is not int for item in raw['data']
    ):
        raise ValueError('Invalid embedding response indices')
    data = sorted(raw['data'], key=lambda item: item['index'])
    if [item['index'] for item in data] != list(range(count)):
        raise ValueError('Invalid embedding response indices')
    vectors = [item['embedding'] for item in data]
    validate_embeddings(vectors, count, dimension)
    return vectors, raw.get('usage') or {}


class Gateway:
    """One protocol implementation, with no NoneBot/configuration side effects."""

    def __init__(self, models: dict[str, ModelSpec]):
        self.models = models

    async def query_llm(
        self, model: str, messages: list[dict], tools=None, options=None
    ) -> ModelTurn:
        import aiohttp

        spec = self.models[model]
        tools, options = tools or [], options or {}
        if tools and not spec.tools:
            raise ValueError(f"Model {model} has no declared tool capability")
        has_images = any(
            isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        if has_images and not spec.multimodal:
            raise ValueError(f"Model {model} has no declared vision capability")
        timeout = aiohttp.ClientTimeout(total=options.get("timeout", 120))
        extra = {**spec.extra, **options.get("extra", {})}
        if spec.protocol == "gemini":
            base = spec.base_url.rstrip("/")
            if not base.endswith(("/v1beta", "/v1", "/models")):
                base += '/' + spec.api_version
            if not base.endswith("/models"):
                base += "/models"
            url = f"{base}/{spec.name.removeprefix('models/')}:generateContent"
            headers = spec.headers()
            payload = gemini_payload(spec.name, messages, tools, {**options, "extra": extra})
        elif spec.protocol == "openai":
            url = spec.base_url.rstrip("/") + "/chat/completions"
            headers = spec.headers()
            payload = {
                "model": spec.name,
                "messages": openai_messages(messages),
                "max_tokens": options.get("max_tokens", 2048),
                **extra,
            }
            if tools:
                payload.update(tools=tools, parallel_tool_calls=spec.parallel_tools)
        else:
            raise ValueError(f"Unknown protocol: {spec.protocol}")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, json=payload, headers=headers, proxy=options.get('proxy')
            ) as response:
                if response.status >= 400:
                    # Do not echo API URLs, credentials, or upstream request bodies.
                    raise RuntimeError(
                        f"{spec.protocol} model request failed: HTTP {response.status}"
                    )
                raw = await response.json()
        return normalize_gemini(raw) if spec.protocol == "gemini" else normalize_openai(raw)

    async def embed(self, model: str, texts: list[str], options=None):
        import aiohttp

        spec = self.models[model]
        options = options or {}
        if spec.protocol == 'gemini':
            base = spec.base_url.rstrip('/')
            if not base.endswith(('/v1', '/v1beta', '/models')):
                base += '/' + spec.api_version
            if not base.endswith('/models'):
                base += '/models'
            name = spec.name.removeprefix('models/')
            url, headers = f'{base}/{name}:batchEmbedContents', spec.headers()
            payload = {
                'requests': [
                    {'model': 'models/' + name, 'content': {'parts': [{'text': text}]}}
                    for text in texts
                ]
            }
        elif spec.protocol == 'openai':
            url, headers = spec.base_url.rstrip('/') + '/embeddings', spec.headers()
            payload = {'model': spec.name, 'input': texts}
        else:
            raise ValueError('Unknown embedding protocol')
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=options.get('timeout', 60))
        ) as session:
            async with session.post(
                url, headers=headers, json=payload, proxy=options.get('proxy')
            ) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Embedding request failed: HTTP {response.status}")
                raw = await response.json()
        if spec.protocol == 'gemini':
            vectors, usage = [d['values'] for d in raw['embeddings']], raw.get(
                'usageMetadata'
            ) or {}
            validate_embeddings(vectors, len(texts), spec.embedding_dimension)
            return vectors, usage
        else:
            return normalize_openai_embeddings(raw, len(texts), spec.embedding_dimension)

"""Tavily provider; credentials are resolved only on the bot side."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import socket
import time
from collections import deque
from urllib.parse import urlsplit

import aiohttp


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in ('https', 'http') or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 80, 443) or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host
    except ValueError:
        return False


async def check_public_url(value):
    if not public_url(value):
        raise ValueError('Only public HTTP(S) URLs are accepted')
    host = urlsplit(value).hostname
    records = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5
    )
    if not records or any(not ipaddress.ip_address(row[4][0]).is_global for row in records):
        raise ValueError('URL does not resolve to a public address')


class TavilyProvider:
    def __init__(self, read_config):
        self.read_config = read_config
        self.requests = deque()

    def settings(self):
        config = dict(self.read_config())
        variable = config.get('api_key_env', '')
        if variable:
            if not isinstance(variable, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', variable):
                raise ValueError('api_key_env must be an environment variable name')
            key = os.environ.get(variable)
            if not key:
                raise ValueError('Configured api_key_env is missing')
        else:
            key = config.get('api_key', '')
        if not isinstance(key, str) or not key.strip() or key in ('xxx', 'CHANGE_ME'):
            raise ValueError('Tavily api_key is not configured')
        config['api_key'] = key
        config.setdefault('base_url', 'https://api.tavily.com')
        config.setdefault('auth_header', 'Authorization')
        config.setdefault('auth_scheme', 'Bearer')
        config.setdefault('qps_limit', 5)
        config.setdefault('timeout', 20)
        if not isinstance(config['auth_header'], str) or not re.fullmatch(r'[A-Za-z0-9-]+', config['auth_header']):
            raise ValueError('Invalid Tavily auth_header')
        if not isinstance(config['auth_scheme'], str) or '\n' in config['auth_scheme'] or '\r' in config['auth_scheme']:
            raise ValueError('Invalid Tavily auth_scheme')
        if config.get('proxy') and (not isinstance(config['proxy'], str) or urlsplit(config['proxy']).scheme not in ('http', 'https')):
            raise ValueError('Invalid Tavily proxy')
        if not isinstance(config['base_url'], str) or urlsplit(config['base_url']).scheme not in ('http', 'https'):
            raise ValueError('Invalid Tavily base_url')
        if type(config['qps_limit']) is not int or config['qps_limit'] < 1:
            raise ValueError('Invalid Tavily qps_limit')
        timeout = config['timeout']
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Invalid Tavily timeout')
        return config

    def describe(self):
        try:
            self.settings()
            return {'available': True, 'provider': 'tavily'}
        except Exception:
            return {'available': False, 'provider': 'tavily', 'error': 'provider_not_configured'}

    async def request(self, endpoint, payload):
        config = self.settings()  # Immutable snapshot for this request; re-read on next call.
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - 1:
            self.requests.popleft()
        if len(self.requests) >= config['qps_limit']:
            raise ValueError('Tavily QPS limit exceeded')
        self.requests.append(now)
        auth = ' '.join(part for part in (config['auth_scheme'], config['api_key']) if part)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=config['timeout'])) as session:
            async with session.post(
                config['base_url'].rstrip('/') + '/' + endpoint,
                headers={config['auth_header']: auth}, json=payload,
                proxy=config.get('proxy') or None, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ValueError(f'Tavily HTTP {response.status}')
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError('Tavily response too large')
                return json.loads(data)

    async def execute(self, method, args):
        try:
            if method == 'web_search':
                query = args['query']
                if not isinstance(query, str) or not query.strip() or len(query) > 500:
                    raise ValueError('Invalid query')
                limit = args.get('limit', 5)
                if type(limit) is not int or not 1 <= limit <= 5:
                    raise ValueError('Invalid limit')
                payload = {'query': query, 'max_results': limit, 'search_depth': 'basic',
                           'include_answer': False, 'include_raw_content': False}
                if args.get('time_range'):
                    if args['time_range'] not in ('day', 'week', 'month', 'year'):
                        raise ValueError('Invalid time_range')
                    payload['time_range'] = args['time_range']
                value = await self.request('search', payload)
                results = []
                for item in value.get('results', [])[:limit]:
                    if public_url(item.get('url')):
                        results.append({
                            'title': str(item.get('title', ''))[:300], 'url': item['url'],
                            'content': str(item.get('content', ''))[:1200],
                            'published_date': str(item.get('published_date') or '')[:100],
                        })
                return {'results': results, 'retrieved_at': time.time(), 'source': 'external_web'}
            if method == 'read_web':
                await check_public_url(args['url'])
                value = await self.request('extract', {'urls': [args['url']], 'extract_depth': 'basic', 'format': 'text'})
                results = value.get('results', [])
                if not results:
                    return {'error': 'page_unavailable', 'url': args['url']}
                content = str(results[0].get('raw_content') or '')
                return {'url': args['url'], 'content': content[:12000], 'truncated': len(content) > 12000,
                        'retrieved_at': time.time(), 'source': 'external_web'}
            return {'error': 'unknown_search_method'}
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return {'error': 'search_network_error'}
        except Exception:
            # Provider responses and exception strings may contain credentials or proxy URLs.
            return {'error': 'search_failed_or_misconfigured'}

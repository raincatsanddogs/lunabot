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

from .search_cache import SearchCache


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


TWITTER_STATUS_RE = re.compile(
    r'^https?://(?:[a-zA-Z0-9-]+\.)?(?:twitter\.com|x\.com|vxtwitter\.com|fixupx\.com|fxtwitter\.com)/([A-Za-z0-9_]+)/status/(\d+)',
    re.IGNORECASE,
)


def format_tweet_content(tweet_data: dict, original_url: str) -> str:
    tweet = tweet_data.get('tweet') if isinstance(tweet_data.get('tweet'), dict) else tweet_data
    author = tweet.get('author') or {}
    author_name = str(author.get('name') or '未知作者')
    screen_name = str(author.get('screen_name') or '')
    author_str = f'{author_name} (@{screen_name})' if screen_name else author_name

    text = str(tweet.get('text') or '').strip()
    created_at = str(tweet.get('created_at') or '')

    lines = [f'【推文作者】: {author_str}']
    if created_at:
        lines.append(f'【发布时间】: {created_at}')
    lines.append(f'【正文】:\n{text if text else "(无纯文本正文)"}')

    media = tweet.get('media') or {}
    all_media = media.get('all') or []
    if all_media and isinstance(all_media, list):
        descs = []
        for idx, m in enumerate(all_media, 1):
            if not isinstance(m, dict):
                continue
            m_type = str(m.get('type') or '媒体')
            alt = str(m.get('altText') or '').strip()
            alt_desc = f' (说明: {alt})' if alt else ''
            url = str(m.get('url') or '')
            descs.append(f'- 附件{idx} [{m_type}]{alt_desc}: {url}')
        if descs:
            lines.append('【媒体附件】:\n' + '\n'.join(descs))

    quote = tweet.get('quote')
    if isinstance(quote, dict) and quote:
        q_author = quote.get('author') or {}
        q_author_name = str(q_author.get('name') or '未知作者')
        q_screen_name = str(q_author.get('screen_name') or '')
        q_author_str = f'{q_author_name} (@{q_screen_name})' if q_screen_name else q_author_name
        q_text = str(quote.get('text') or '').strip()
        lines.append(f'【引用的推文】 ({q_author_str}):\n{q_text if q_text else "(无纯文本正文)"}')

    likes = tweet.get('likes')
    retweets = tweet.get('retweets')
    replies = tweet.get('replies')
    stats = []
    if likes is not None:
        stats.append(f'喜欢: {likes}')
    if retweets is not None:
        stats.append(f'转推: {retweets}')
    if replies is not None:
        stats.append(f'回复: {replies}')
    if stats:
        lines.append(f'【互动统计】: {" | ".join(stats)}')

    return '\n\n'.join(lines)


class TavilyProvider:
    def __init__(self, read_config, cache: SearchCache | None = None):
        self.read_config = read_config
        self.requests = deque()
        self.cache = cache or SearchCache()

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

    async def _fetch_twitter_status(self, user: str, status_id: str, original_url: str, config: dict):
        api_url = f'https://api.fxtwitter.com/{user}/status/{status_id}'
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; LunaBot/1.0; +https://github.com)',
            'Accept': 'application/json',
        }
        timeout_sec = min(float(config.get('timeout', 10)), 15.0)
        proxy = config.get('proxy') or None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_sec)) as session:
            async with session.get(api_url, headers=headers, proxy=proxy, allow_redirects=True) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if not isinstance(data, dict) or data.get('code') != 200:
                    return None
                tweet = data.get('tweet')
                if not isinstance(tweet, dict):
                    return None
                content = format_tweet_content(tweet, original_url)
                return {
                    'url': original_url,
                    'content': content[:12000],
                    'truncated': len(content) > 12000,
                    'retrieved_at': time.time(),
                    'source': 'fxtwitter_api',
                }

    async def execute(self, method, args):
        try:
            config = self.settings()
            use_cache = self.cache is not None and config.get('cache', True)

            if method == 'search_web':
                query = args['query']
                if not isinstance(query, str) or not query.strip() or len(query) > 500:
                    raise ValueError('Invalid query')
                limit = args.get('limit', 5)
                if type(limit) is not int or not 1 <= limit <= 5:
                    raise ValueError('Invalid limit')

                if use_cache:
                    cached = self.cache.get('search_web', query)
                    if cached is not None:
                        results = cached.get('results', [])[:limit]
                        return {**cached, 'results': results}

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
                resp = {'results': results, 'retrieved_at': time.time(), 'source': 'external_web'}
                if use_cache:
                    self.cache.set('search_web', query, resp)
                return resp
            if method == 'read_web':
                url = args['url']
                if use_cache:
                    cached = self.cache.get('read_web', url)
                    if cached is not None:
                        return cached

                await check_public_url(url)
                tw_match = TWITTER_STATUS_RE.match(url)
                if tw_match:
                    try:
                        tw_resp = await self._fetch_twitter_status(
                            tw_match.group(1), tw_match.group(2), url, config
                        )
                        if tw_resp:
                            if use_cache:
                                self.cache.set('read_web', url, tw_resp)
                            return tw_resp
                    except Exception:
                        pass

                value = await self.request('extract', {'urls': [url], 'extract_depth': 'basic', 'format': 'text'})
                results = value.get('results', [])
                if not results:
                    return {'error': 'page_unavailable', 'url': url}
                content = str(results[0].get('raw_content') or '')
                resp = {'url': url, 'content': content[:12000], 'truncated': len(content) > 12000,
                        'retrieved_at': time.time(), 'source': 'external_web'}
                if use_cache:
                    self.cache.set('read_web', url, resp)
                return resp
            return {'error': 'unknown_search_method'}
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return {'error': 'search_network_error'}
        except Exception:
            # Provider responses and exception strings may contain credentials or proxy URLs.
            return {'error': 'search_failed_or_misconfigured'}

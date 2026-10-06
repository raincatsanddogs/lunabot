"""Search and web extract caching layer with normalized exact and protected fuzzy matching."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path


def normalize_query(text: str) -> str:
    """Normalize query text with synonym replacement and punctuation cleaning."""
    lower_text = text.lower().strip()
    # Normalize common query intent synonyms
    lower_text = re.sub(r'发售时间|上市时间|发售日期|上市日期|何时发售|什么时候发售', '发售日', lower_text)
    lower_text = re.sub(r'更新时间|更新日期|何时更新|什么时候更新', '更新日', lower_text)
    lower_text = re.sub(r'播出时间|上映时间|播出日期|上映日期', '上映日', lower_text)
    lower_text = re.sub(r'多少钱|价格|售价', '价格', lower_text)
    return lower_text


def tokenize(text: str) -> tuple[set[str], set[str], str]:
    """Extract tokens, numbers, and canonical key from query text."""
    normalized = normalize_query(text)
    # Extract numeric entities (years, version numbers, model numbers) without word boundary issues on CJK
    numbers = set(re.findall(r'(?<!\d)\d+(?:\.\d+)?(?!\d)', normalized))
    # Extract alphanumeric words and CJK characters
    tokens = set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fa5]', normalized))
    canonical_str = ' '.join(sorted(tokens))
    canonical_key = hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()
    return tokens, numbers, canonical_key


def calculate_ttl(method: str, query: str) -> int:
    """Determine cache TTL based on query volatility and historical nature."""
    if method == 'read_web':
        return 86400  # 24 hours for static web pages
    norm = normalize_query(query)
    # Highly volatile topics: weather, stock price, breaking today news
    if re.search(r'今天|现在|最新|天气|实时|股价|汇率|金价|今日|当前', norm):
        return 900  # 15 minutes
    # Fixed historical facts or confirmed release dates (contains explicit year or launch terms)
    if re.search(r'(?<!\d)(19\d\d|20[0-2]\d)(?!\d)|发售日|发售时间|上市时间|历史|何时发售|几几年|第\d+届', norm):
        return 604800  # 7 days
    # Regular default
    return 21600  # 6 hours


def compute_similarity(tokens1: set[str], numbers1: set[str], tokens2: set[str], numbers2: set[str]) -> float:
    """Compute similarity between two queries under strict entity protection."""
    if numbers1 != numbers2:
        return 0.0
    if not tokens1 or not tokens2:
        return 0.0
    intersection = len(tokens1 & tokens2)
    jaccard = intersection / len(tokens1 | tokens2)
    containment = intersection / min(len(tokens1), len(tokens2))
    # If one query is almost a complete subset of another with sufficient length (>=4 tokens)
    if min(len(tokens1), len(tokens2)) >= 4 and containment >= 0.95:
        return max(jaccard, 0.9)
    return jaccard


class SearchCache:
    """SQLite + L1 LRU memory cache for web search and page extract results."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        max_memory_entries: int = 200,
        similarity_threshold: float = 0.75,
    ):
        if db_path is None:
            db_path = Path('data/chat/autochat/search_cache.sqlite')
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_memory = max_memory_entries
        self.threshold = similarity_threshold
        self.l1_cache: OrderedDict[str, dict] = OrderedDict()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute('PRAGMA journal_mode = WAL;')
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    method TEXT NOT NULL,
                    query TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    numbers_json TEXT NOT NULL,
                    tokens_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expire_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0
                );
                """
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_search_cache_lookup ON search_cache(method, canonical_key, expire_at);'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_search_cache_expire ON search_cache(expire_at);'
            )

    def clean_expired(self):
        try:
            now = time.time()
            with self._get_connection() as conn:
                conn.execute('DELETE FROM search_cache WHERE expire_at <= ?;', (now,))
        except Exception:
            pass

    def get(self, method: str, query: str) -> dict | None:
        """Look up cache by exact canonical key first, then by entity-protected fuzzy similarity."""
        now = time.time()
        tokens, numbers, canonical_key = tokenize(query)
        mem_key = f'{method}:{canonical_key}'

        # 1. L1 Memory exact match
        if mem_key in self.l1_cache:
            entry = self.l1_cache[mem_key]
            if entry['expire_at'] > now:
                self.l1_cache.move_to_end(mem_key)
                return self._format_result(entry['result'], entry['created_at'], entry['query'])
            else:
                del self.l1_cache[mem_key]

        # 2. SQLite exact match
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, query, result_json, created_at, expire_at
                    FROM search_cache
                    WHERE method = ? AND canonical_key = ? AND expire_at > ?
                    ORDER BY id DESC LIMIT 1;
                    """,
                    (method, canonical_key, now),
                ).fetchone()

                if row:
                    conn.execute('UPDATE search_cache SET hit_count = hit_count + 1 WHERE id = ?;', (row['id'],))
                    result = json.loads(row['result_json'])
                    self._update_l1(mem_key, result, row['created_at'], row['expire_at'], row['query'])
                    return self._format_result(result, row['created_at'], row['query'])

                # 3. Protected fuzzy similarity match (search_web only)
                if method == 'search_web' and tokens:
                    candidates = conn.execute(
                        """
                        SELECT id, query, numbers_json, tokens_json, result_json, created_at, expire_at
                        FROM search_cache
                        WHERE method = ? AND expire_at > ?
                        ORDER BY id DESC LIMIT 100;
                        """,
                        (method, now),
                    ).fetchall()

                    best_match = None
                    highest_sim = 0.0

                    for cand in candidates:
                        cand_numbers = set(json.loads(cand['numbers_json']))
                        cand_tokens = set(json.loads(cand['tokens_json']))
                        sim = compute_similarity(tokens, numbers, cand_tokens, cand_numbers)
                        if sim >= self.threshold and sim > highest_sim:
                            highest_sim = sim
                            best_match = cand

                    if best_match is not None:
                        conn.execute('UPDATE search_cache SET hit_count = hit_count + 1 WHERE id = ?;', (best_match['id'],))
                        result = json.loads(best_match['result_json'])
                        self._update_l1(mem_key, result, best_match['created_at'], best_match['expire_at'], best_match['query'])
                        return self._format_result(result, best_match['created_at'], best_match['query'], fuzzy_jaccard=highest_sim)
        except Exception:
            return None

        return None

    def set(self, method: str, query: str, result: dict, ttl: int | None = None):
        """Store result into L1 memory and SQLite."""
        if not isinstance(result, dict) or 'error' in result:
            return
        now = time.time()
        if ttl is None:
            ttl = calculate_ttl(method, query)
        expire_at = now + ttl
        tokens, numbers, canonical_key = tokenize(query)
        mem_key = f'{method}:{canonical_key}'

        self._update_l1(mem_key, result, now, expire_at, query)

        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO search_cache (
                        method, query, canonical_key, numbers_json, tokens_json, result_json, created_at, expire_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        method,
                        query,
                        canonical_key,
                        json.dumps(sorted(numbers), ensure_ascii=False),
                        json.dumps(sorted(tokens), ensure_ascii=False),
                        json.dumps(result, ensure_ascii=False),
                        now,
                        expire_at,
                    ),
                )
                # 5% chance to trigger cleanup of expired records
                if int(now) % 20 == 0:
                    conn.execute('DELETE FROM search_cache WHERE expire_at <= ?;', (now,))
        except Exception:
            pass

    def _update_l1(self, mem_key: str, result: dict, created_at: float, expire_at: float, query: str):
        self.l1_cache[mem_key] = {
            'result': result,
            'created_at': created_at,
            'expire_at': expire_at,
            'query': query,
        }
        if len(self.l1_cache) > self.max_memory:
            self.l1_cache.popitem(last=False)

    def _format_result(
        self,
        result: dict,
        created_at: float,
        matched_query: str,
        fuzzy_jaccard: float | None = None,
    ) -> dict:
        formatted = dict(result)
        formatted['cached'] = True
        formatted['cache_age_seconds'] = max(0, int(time.time() - created_at))
        if matched_query:
            formatted['matched_query'] = matched_query
        if fuzzy_jaccard is not None:
            formatted['fuzzy_similarity'] = round(fuzzy_jaccard, 3)
        return formatted

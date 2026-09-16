from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re


class MemoryIndex:
    """Disposable cosine index; SQLite owns sources, subjects and record status."""

    def __init__(self, store, gateway, model, clock):
        self.store, self.gateway, self.model, self.clock = store, gateway, model, clock
        self.client = None
        self.lock = asyncio.Lock()
        self.store.db.execute(
            'CREATE TABLE IF NOT EXISTS embedding_cache (key TEXT PRIMARY KEY, vector TEXT NOT NULL)'
        )
        self.store.db.commit()
        if model:
            import chromadb
            from chromadb.config import Settings

            self.client = chromadb.PersistentClient(
                path=str(store.root / 'vectors'), settings=Settings(anonymized_telemetry=False)
            )

    def fingerprint(self):
        spec = getattr(self.gateway, 'models', {}).get(self.model)
        fields = [
            getattr(spec, key, None)
            for key in (
                'name',
                'protocol',
                'base_url',
                'api_version',
                'embedding_dimension',
                'query_instruction',
            )
        ]
        return hashlib.sha256(
            json.dumps([self.model, fields, 'cosine-query-v1'], ensure_ascii=False).encode()
        ).hexdigest()

    def collection(self, scope):
        identity = scope.key + self.fingerprint()
        return self.client.get_or_create_collection(
            'mem_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
            metadata={'hnsw:space': 'cosine'},
        )

    async def vectors(self, scope, texts, task):
        spec = getattr(self.gateway, 'models', {}).get(self.model)
        instruction = getattr(spec, 'query_instruction', '')
        inputs = [
            (
                ('Instruct: ' + instruction + '\nQuery: ' + text)
                if task == 'search' and instruction
                else text
            )
            for text in texts
        ]
        keys = [
            hashlib.sha256((self.fingerprint() + '\0' + task + '\0' + text).encode()).hexdigest()
            for text in inputs
        ]
        async with self.lock:
            result, missing = {}, {}
            for key, text in zip(keys, inputs):
                row = self.store.db.execute(
                    'SELECT vector FROM embedding_cache WHERE key=?', (key,)
                ).fetchone()
                if row:
                    result[key] = json.loads(row[0])
                else:
                    missing[key] = text
            if result:
                self.store.trace(
                    self.clock.now(),
                    scope,
                    'embedding_cache',
                    {'task': task, 'hits': sum(k in result for k in keys)},
                )
            if missing:
                self.store.trace(
                    self.clock.now(),
                    scope,
                    'embedding_request',
                    {'model': self.model, 'count': len(missing), 'task': task},
                )
                try:
                    vectors, usage = await self.gateway.embed(self.model, list(missing.values()))
                    dimension = getattr(spec, 'embedding_dimension', None)
                    if (
                        len(vectors) != len(missing)
                        or not vectors
                        or len({len(v) for v in vectors}) != 1
                    ):
                        raise ValueError('Invalid embedding shape')
                    for key, vector in zip(missing, vectors):
                        if (
                            not vector
                            or (dimension and len(vector) != dimension)
                            or any(
                                isinstance(v, bool)
                                or not isinstance(v, (int, float))
                                or not math.isfinite(v)
                                for v in vector
                            )
                        ):
                            raise ValueError('Invalid embedding vector')
                        norm = math.sqrt(sum(v * v for v in vector))
                        if not norm or not math.isfinite(norm):
                            raise ValueError('Invalid embedding norm')
                        result[key] = [v / norm for v in vector]
                except Exception as exc:
                    self.store.trace(
                        self.clock.now(),
                        scope,
                        'embedding_failed',
                        {'model': self.model, 'task': task, 'type': type(exc).__name__},
                    )
                    raise
                with self.store.db:
                    self.store.db.executemany(
                        'INSERT OR REPLACE INTO embedding_cache VALUES (?,?)',
                        [(k, json.dumps(result[k])) for k in missing],
                    )
                self.store.trace(
                    self.clock.now(),
                    scope,
                    'embedding',
                    {'usage': usage, 'count': len(missing), 'task': task},
                )
            return [result[k] for k in keys]

    def degraded(self, scope, exc, fallback):
        reason = 'embedding 请求超时' if isinstance(exc, TimeoutError) else type(exc).__name__
        if isinstance(exc, ValueError) or str(exc).startswith('Embedding request failed: HTTP '):
            reason = str(exc)[:200]
        self.store.trace(
            self.clock.now(),
            scope,
            'index_error',
            {'type': type(exc).__name__, 'reason': reason, 'fallback': fallback},
        )

    async def sync(self, scope):
        """同步当前版本；网络等待期间被修改的记录不能写回旧向量。"""
        if not self.client:
            return
        records = self.store.memories(
            scope, statuses=('active', 'candidate'), limit=10000, now=self.clock.now()
        )
        collection = self.collection(scope)
        current = collection.get(include=['metadatas'])
        known = dict(zip(current['ids'], current['metadatas']))
        ids = {r['id'] for r in records}
        stale = set(known) - ids
        if stale:
            collection.delete(ids=list(stale))
        pending = [r for r in records if (known.get(r['id']) or {}).get('version') != r['version']]
        for start in range(0, len(pending), 32):
            batch = pending[start : start + 32]
            embeddings = await self.vectors(scope, [r['content'] for r in batch], 'index')
            current_records = {
                r['id']: r
                for r in self.store.memories(
                    scope, statuses=('active', 'candidate'), limit=10000, now=self.clock.now()
                )
            }
            valid = [
                (r, vector)
                for r, vector in zip(batch, embeddings)
                if r['id'] in current_records
                and current_records[r['id']]['version'] == r['version']
            ]
            invalid = [
                r['id']
                for r in batch
                if r['id'] not in current_records
                or current_records[r['id']]['version'] != r['version']
            ]
            if invalid:
                collection.delete(ids=invalid)
            if valid:
                collection.upsert(
                    ids=[r['id'] for r, _ in valid],
                    embeddings=[v for _, v in valid],
                    metadatas=[{'version': r['version']} for r, _ in valid],
                )

    async def search(self, scope, query, subjects=None, include_unverified=False):
        statuses = ('active', 'candidate') if include_unverified else ('active',)
        allowed = self.store.memories(
            scope, subjects, statuses=statuses, limit=10000, now=self.clock.now()
        )
        if self.client and allowed:
            try:
                await self.sync(scope)
                embeddings = await self.vectors(scope, [query], 'search')
                collection = self.collection(scope)
                if collection.count():
                    result = collection.query(
                        query_embeddings=embeddings,
                        n_results=min(100, collection.count()),
                        include=['metadatas'],
                    )
                    by_id = {
                        m['id']: m
                        for m in self.store.memories(
                            scope, subjects, statuses=statuses, limit=10000, now=self.clock.now()
                        )
                    }
                    matches = [
                        by_id[mid]
                        for mid, metadata in zip(result['ids'][0], result['metadatas'][0])
                        if mid in by_id and (metadata or {}).get('version') == by_id[mid]['version']
                    ]
                    if matches:
                        return matches[:10]
            except Exception as exc:
                self.degraded(scope, exc, 'text_search')
        terms = set(re.findall(r'\w+', query.lower())) | set(query.lower())
        allowed = self.store.memories(
            scope, subjects, statuses=statuses, limit=10000, now=self.clock.now()
        )
        return sorted(
            allowed, key=lambda m: len(terms.intersection(set(m['content'].lower()))), reverse=True
        )[:10]

    async def duplicates(self, scope, proposal):
        """Find review candidates only; similarity never authorizes a merge."""
        if not self.client:
            return []
        compatible = [
            m
            for m in self.store.memories(
                scope,
                proposal['subject_ids'],
                statuses=('active', 'candidate'),
                now=self.clock.now(),
            )
            if set(m['subject_ids']) == set(proposal['subject_ids'])
            and m['kind'] == proposal['kind']
        ]
        if not compatible:
            return []
        try:
            await self.sync(scope)
            vector = await self.vectors(scope, [proposal['content']], 'index')
            collection = self.collection(scope)
            result = collection.query(
                query_embeddings=vector,
                n_results=min(20, collection.count()),
                include=['distances', 'metadatas'],
            )
            by_id = {
                m['id']: m
                for m in self.store.memories(
                    scope,
                    proposal['subject_ids'],
                    statuses=('active', 'candidate'),
                    now=self.clock.now(),
                )
                if set(m['subject_ids']) == set(proposal['subject_ids'])
                and m['kind'] == proposal['kind']
            }
            return [
                by_id[mid]
                for mid, distance, metadata in zip(
                    result['ids'][0], result['distances'][0], result['metadatas'][0]
                )
                if mid in by_id
                and distance <= 0.1
                and (metadata or {}).get('version') == by_id[mid]['version']
            ]
        except Exception as exc:
            self.degraded(scope, exc, 'text_duplicate_check')
            return []

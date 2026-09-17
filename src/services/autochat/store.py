from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from difflib import SequenceMatcher

from .types import Event, Scope


def dump(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


# 入库证据校验与数据库写入放在一起；结构检查不能替代语义判断。
def normalized(text):
    return re.sub(r'[\s，。！？,.!?；;：:]', '', text).lower()


def negative(text):
    return bool(re.search(r'不|没|无|否认|讨厌|never|not\b|dislike', text, re.I))


def check_evidence(proposal, sources, subjects):
    by_id = {e.message_id: e for e in sources}
    refs = proposal.get('evidence')
    if not isinstance(refs, list) or not refs or {r.get('message_id') for r in refs} != set(by_id):
        raise ValueError('Evidence must cover every source message')
    for ref in refs:
        quote = ref.get('quote')
        if (
            not isinstance(quote, str)
            or len(quote.strip()) < 2
            or quote not in by_id[ref['message_id']].text
        ):
            raise ValueError('Evidence quote is not present in the source text')
    direct = (
        len(subjects) == 1
        and proposal.get('evidence_type') == 'self_report'
        and {r['message_id'] for r in refs} == set(by_id)
        and all(e.speaker_id == subjects[0] for e in sources)
    )
    for ref in refs:
        event, quote = by_id[ref['message_id']], ref['quote']
        direct = direct and bool(re.search(r'我|本人|\bI\b|\bmy\b', quote, re.I))
        direct = direct and not re.search(
            r'[?？]|[吗呢么][。！!…]*$|\b(?:can|could|do|does|is|are)\s+you\b', quote, re.I
        )
        # 裁剪出来的引文不能把问句中的预设变成确定陈述。
        start = event.text.find(quote)
        end = start + len(quote)
        boundary = re.search(r'[。！？.!?\n]', event.text[end:])
        clause_end = end + boundary.end() if boundary else len(event.text)
        direct = direct and not re.search(
            r'[?？]|[吗呢么][。！!…]*$', event.text[start:clause_end], re.I
        )
        direct = direct and not any(
            s['type'] in ('reply', 'forward', 'node', 'json', 'xml') for s in event.segments
        )
        direct = direct and not re.search(
            r'听说|据说|转述|转发|引用|截图|[\w\u4e00-\u9fff]+说[：:]', event.text
        )
        direct = direct and not any(mark in event.text for mark in ('“', '”', '「', '」', '"', '>'))
    return refs, bool(direct)


def same_evidence(left, right):
    return bool(left) and {(r['message_id'], normalized(r['quote'])) for r in left} == {
        (r['message_id'], normalized(r['quote'])) for r in right
    }


def near(left, right):
    a, b = normalized(left), normalized(right)
    return a == b or (negative(a) == negative(b) and SequenceMatcher(None, a, b).ratio() >= 0.65)


class Store:
    """权威 SQLite 存储：事件、批次、发送动作、记忆及其版本历史。"""

    def __init__(self, root, exclusive=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = None
        if exclusive:
            import os

            self.lock_file = (self.root / 'service.lock').open('a+b')
            try:
                if os.name == 'nt':
                    import msvcrt

                    self.lock_file.seek(0)
                    self.lock_file.write(b'0')
                    self.lock_file.flush()
                    self.lock_file.seek(0)
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                self.lock_file.close()
                raise RuntimeError('Another autochat process is using this data directory') from exc
        self.db = sqlite3.connect(self.root / "autochat.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.audit = {'origin': 'model'}
        self.db.create_function('memory_audit', 0, lambda: dump(self.audit))
        self.db.create_function('audit_time', 0, lambda: self.audit.get('time', time.time()))
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                message_id TEXT NOT NULL, speaker TEXT NOT NULL, time REAL NOT NULL,
                payload TEXT NOT NULL, handled INTEGER NOT NULL DEFAULT 0,
                received REAL NOT NULL, UNIQUE(scope,message_id));
            CREATE INDEX IF NOT EXISTS event_scope ON events(scope,seq);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, value TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                payload TEXT NOT NULL, time REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_subjects (
                memory_id TEXT REFERENCES memories(id), subject TEXT NOT NULL,
                PRIMARY KEY(memory_id,subject));
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS trace (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL,
                scope TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, mime TEXT NOT NULL, size INTEGER NOT NULL,
                created REAL NOT NULL, last_used REAL NOT NULL, available INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS stickers (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, asset_id TEXT NOT NULL,
                status TEXT NOT NULL, metadata TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                last_sent REAL, UNIQUE(scope,asset_id));
            CREATE TABLE IF NOT EXISTS sticker_operations (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
        """
        )
        self.db.commit()
        # 当前记录作为历史基线；后续插入和更新由触发器在同一事务内留档。
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_history (
                id TEXT NOT NULL, version INTEGER NOT NULL, scope TEXT NOT NULL,
                kind TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, changed_at REAL NOT NULL, audit TEXT NOT NULL,
                PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS memory_operations (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_guards (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, memory_id TEXT NOT NULL, payload TEXT NOT NULL);
            INSERT OR IGNORE INTO memory_history
                SELECT id,version,scope,kind,content,status,payload,time,'{"origin":"baseline"}' FROM memories;
            CREATE TRIGGER IF NOT EXISTS memory_insert_history AFTER INSERT ON memories BEGIN
                INSERT INTO memory_history VALUES (NEW.id,NEW.version,NEW.scope,NEW.kind,NEW.content,NEW.status,
                    NEW.payload,audit_time(),memory_audit());
            END;
            CREATE TRIGGER IF NOT EXISTS memory_update_history AFTER UPDATE ON memories BEGIN
                INSERT INTO memory_history VALUES (NEW.id,NEW.version,NEW.scope,NEW.kind,NEW.content,NEW.status,
                    NEW.payload,audit_time(),memory_audit());
            END;
        """
        )

    def close(self):
        self.db.close()
        if self.lock_file:
            self.lock_file.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, dump(value)),
            )

    def add_event(self, event: Event, now: float):
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO events(scope,message_id,speaker,time,payload,received) VALUES (?,?,?,?,?,?)",
                (
                    event.scope.key,
                    event.message_id,
                    event.speaker_id,
                    event.time,
                    dump(event.to_dict()),
                    now,
                ),
            )
        if not cursor.rowcount:
            return None
        event.seq = cursor.lastrowid
        return event

    def update_event(self, event):
        with self.db:
            self.db.execute(
                'UPDATE events SET payload=? WHERE scope=? AND message_id=?',
                (dump(event.to_dict()), event.scope.key, event.message_id),
            )

    def events(
        self,
        scope: Scope,
        *,
        after=0,
        before=None,
        limit=100,
        pending=False,
        now=None,
        ids=None,
        latest=False,
    ):
        where, args = ["scope=?", "seq>?"], [scope.key, after]
        if before is not None:
            where.append("seq<=?")
            args.append(before)
        if now is not None:
            where.append("time<=?")
            args.append(now)
        if pending:
            where.append("handled=0")
        if ids is not None:
            if not ids:
                return []
            where.append("message_id IN (" + ",".join("?" for _ in ids) + ")")
            args.extend(map(str, ids))
        rows = self.db.execute(
            "SELECT seq,payload FROM events WHERE "
            + " AND ".join(where)
            + " ORDER BY seq "
            + ('DESC' if latest else 'ASC')
            + " LIMIT ?",
            [*args, limit],
        ).fetchall()
        if latest:
            rows.reverse()
        result = []
        for row in rows:
            event = Event.from_dict(json.loads(row["payload"]))
            event.seq = row["seq"]
            result.append(event)
        return result

    def scopes(self):
        return [
            Scope(*row[0].split(":", 1))
            for row in self.db.execute("SELECT scope FROM events UNION SELECT scope FROM memories")
        ]

    def mark_handled(self, events):
        with self.db:
            self.db.executemany(
                "UPDATE events SET handled=1 WHERE seq=?", [(e.seq,) for e in events]
            )

    def state(self, scope):
        defaults = {
            "policy": {},
            "wakes": [],
            "calls": [],
            "summary_calls": [],
            "summary_cursor": 0,
            "context_cursor": 0,
            "context": [],
            "epoch": 0,
            "visible_sources": [],
            "awaiting": {},
            "contacts": {},
            "contact_cursor": 0,
            "unanswered": 0,
        }
        return {**defaults, **self.get("state:" + scope.key, {})}

    def save_state(self, scope, state):
        self.set("state:" + scope.key, state)

    def commit_finish(self, scope, state, batch_id, result):
        with self.db:
            for key, value in [('state:' + scope.key, state), ('result:' + batch_id, result)]:
                self.db.execute(
                    'INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (key, dump(value)),
                )

    def trace(self, now, scope, kind, payload):
        with self.db:
            self.db.execute(
                "INSERT INTO trace(time,scope,kind,payload) VALUES (?,?,?,?)",
                (now, scope.key, kind, dump(payload)),
            )

    def traces(self, after=0, limit=1000):
        return [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in self.db.execute(
                "SELECT * FROM trace WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)
            )
        ]

    def batch(self, scope, events, value):
        batch_id = hashlib.sha256(
            dump([scope.key, [e.message_id for e in events]]).encode()
        ).hexdigest()[:24]
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO batches VALUES (?,?,?,?)",
                (batch_id, scope.key, dump(value), "pending"),
            )
        row = self.db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        return batch_id, json.loads(row["value"]), row["status"]

    def batch_done(self, batch_id, status):
        with self.db:
            self.db.execute("UPDATE batches SET status=? WHERE id=?", (status, batch_id))

    def action(self, action_id):
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return (
            {
                **dict(row),
                "payload": json.loads(row["payload"]),
                "result": json.loads(row["result"]) if row["result"] else None,
            }
            if row
            else None
        )

    def start_action(self, action_id, scope, payload):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO actions VALUES (?,?,?,?,NULL)",
                (action_id, scope.key, "pending", dump(payload)),
            )
        return self.action(action_id)

    def finish_action(self, action_id, state, result):
        with self.db:
            self.db.execute(
                "UPDATE actions SET state=?,result=? WHERE id=?", (state, dump(result), action_id)
            )

    def recover_actions(self):
        with self.db:
            self.db.execute("UPDATE actions SET state='unknown' WHERE state='sending'")

    def memories(self, scope, subjects=None, statuses=("active",), limit=100, now=None):
        where, args = ["m.scope=?"], [scope.key]
        if statuses:
            where.append("m.status IN (" + ",".join("?" for _ in statuses) + ")")
            args.extend(statuses)
        if subjects:
            where.append(
                "EXISTS (SELECT 1 FROM memory_subjects s WHERE s.memory_id=m.id AND s.subject IN ("
                + ",".join("?" for _ in subjects)
                + "))"
            )
            args.extend(map(str, subjects))
        if now is not None:
            where.append("m.time<=?")
            args.append(now)
        rows = self.db.execute(
            "SELECT m.* FROM memories m WHERE "
            + " AND ".join(where)
            + " ORDER BY time DESC,id LIMIT ?",
            [*args, limit],
        ).fetchall()
        return [
            {
                **json.loads(r["payload"]),
                "id": r["id"],
                "content": r["content"],
                "kind": r["kind"],
                "status": r["status"],
                "version": r["version"],
            }
            for r in rows
        ]

    def propose(self, scope, proposal, now, allowed_sources=None, possible_duplicates=()):
        self.audit = {'origin': 'model', 'time': now}
        subjects = list(dict.fromkeys(str(x) for x in proposal.get("subject_ids", [])))
        source_ids = list(dict.fromkeys(str(x) for x in proposal.get("source_message_ids", [])))
        content = str(proposal.get("content", "")).strip()
        kind = proposal.get("kind")
        if (
            not subjects
            or len(subjects) > 8
            or not source_ids
            or len(source_ids) > 20
            or not content
            or len(content) > 2000
            or kind not in ("fact", "event", "impression")
        ):
            raise ValueError("Memory requires valid subjects, kind, content and sources")
        if allowed_sources is not None and not set(source_ids).issubset(allowed_sources):
            raise ValueError("Source was not visible to this model turn")
        sources = self.events(scope, ids=source_ids, now=now, limit=20)
        if len(sources) != len(source_ids):
            raise ValueError("Source missing or outside scope/time")
        known = {e.speaker_id for e in self.events(scope, limit=100000, now=now)}
        for event in sources:
            known.update(
                str(s.get("data", {}).get("qq")) for s in event.segments if s.get("type") == "at"
            )
        if not set(subjects).issubset(known):
            raise ValueError("Unknown subject")
        # Structural checks cannot establish semantic truth. Quotes/forwarded images
        # and third-party reports never automatically become the subject's facts.
        evidence, direct = check_evidence(proposal, sources, subjects)
        for guard in self.guards(scope):
            if set(guard['subject_ids']) != set(subjects) or guard['kind'] != kind:
                continue
            for old_ref in guard.get('evidence', []):
                for ref in evidence:
                    if old_ref['message_id'] == ref['message_id'] and near(
                        content, guard['content']
                    ):
                        a, b = normalized(old_ref['quote']), normalized(ref['quote'])
                        if a in b or b in a:
                            raise ValueError(
                                'Source fragment was manually withdrawn; use a new personal statement'
                            )
        status = "active" if direct and kind != "impression" else "candidate"
        value = {
            **proposal,
            "bot_id": scope.bot_id,
            "group_id": scope.group_id,
            "subject_ids": subjects,
            "source_message_ids": source_ids,
            "speaker_ids": sorted({e.speaker_id for e in sources}),
            "created_at": now,
            'evidence': evidence,
        }
        related = [
            m
            for m in self.memories(
                scope, subjects, statuses=('active', 'candidate'), limit=10000, now=now
            )
            if set(m['subject_ids']) == set(subjects) and m['kind'] == kind
        ]

        def references(ref):
            row = self.db.execute(
                'SELECT * FROM memories WHERE scope=? AND id=?', (scope.key, ref['id'])
            ).fetchone()
            if (
                not row
                or row['version'] != ref['version']
                or set(json.loads(row['payload'])['subject_ids']) != set(subjects)
            ):
                raise ValueError('Memory reference has wrong scope, subject or version')
            return row

        duplicate = proposal.get('duplicate_of')
        if duplicate:
            row = references(duplicate)
            old = json.loads(row['payload'])
            old_evidence = old.get('evidence', [])
            if old.get('manual_barrier'):
                raise ValueError(
                    'Manual memory requires a new personal statement and supersedes, not duplicate_of'
                )
            compatible_evidence = same_evidence(evidence, old_evidence) or (
                direct and row['status'] == 'active'
            )
            if (
                row['status'] not in ('active', 'candidate')
                or row['kind'] != kind
                or not compatible_evidence
                or not near(content, row['content'])
                or negative(content) != negative(row['content'])
            ):
                raise ValueError(
                    'Duplicate requires compatible evidence and meaning; a correction must not be merged'
                )
            # No promotion, replacement or loss of original provenance on a duplicate.
            with self.db:
                combined = old_evidence + [ref for ref in evidence if ref not in old_evidence]
                version = row['version']
                if combined != old_evidence:
                    old['evidence'] = combined
                    old['source_message_ids'] = list(
                        dict.fromkeys(old['source_message_ids'] + source_ids)
                    )
                    old['speaker_ids'] = sorted(
                        set(old.get('speaker_ids', [])) | {e.speaker_id for e in sources}
                    )
                    version += 1
                    self.db.execute(
                        'UPDATE memories SET payload=?,version=? WHERE id=?',
                        (dump(old), version, row['id']),
                    )
                for pending in self.memories(
                    scope, subjects, statuses=('pending_review',), limit=10000
                ):
                    if (
                        set(pending['subject_ids']) == set(subjects)
                        and same_evidence(evidence, pending.get('evidence', []))
                        and any(
                            ref['id'] == row['id'] for ref in pending.get('possible_duplicates', [])
                        )
                    ):
                        pending['merged_into'] = row['id']
                        self.db.execute(
                            "UPDATE memories SET status='merged',version=version+1,payload=? WHERE id=?",
                            (dump(pending), pending['id']),
                        )
            self.trace(now, scope, 'memory_merge', {'target_id': row['id'], 'sources': source_ids})
            return {'id': row['id'], 'status': 'duplicate', 'version': version}
        distinct = {references(ref)['id'] for ref in proposal.get('distinct_from', [])}
        reviewed_ids = distinct | {
            ref['id'] for ref in proposal.get('resolves', []) + proposal.get('supersedes', [])
        }
        neighbor_ids = {r['id'] for r in possible_duplicates}
        similar = [
            m
            for m in related
            if m['id'] not in reviewed_ids
            and (
                (
                    set(m['source_message_ids']) == set(source_ids)
                    and (
                        same_evidence(evidence, m.get('evidence', []))
                        or near(content, m['content'])
                    )
                    and content != m['content']
                )
                or (
                    m['id'] in neighbor_ids
                    and (content != m['content'] or set(m['source_message_ids']) != set(source_ids))
                )
            )
        ]
        resolutions = [(references(ref), ref['outcome']) for ref in proposal.get('resolves', [])]
        for old, outcome in resolutions:
            old_sources = self.events(
                scope, ids=json.loads(old['payload']).get('source_message_ids', [])
            )
            if (
                status != 'active'
                or old['status'] not in ('candidate', 'pending_review')
                or outcome not in ('confirmed', 'refuted')
            ):
                raise ValueError(
                    'Candidate resolution requires a supported personal statement and a pending candidate'
                )
            if old_sources and max(e.seq for e in sources) <= max(e.seq for e in old_sources):
                raise ValueError('Resolution must use newer evidence')
        if similar:
            status = 'pending_review'
            value['possible_duplicates'] = [
                {'id': m['id'], 'version': m['version'], 'content': m['content']}
                for m in similar[:5]
            ]
        memory_id = hashlib.sha256(
            dump([scope.key, subjects, kind, content, source_ids]).encode()
        ).hexdigest()[:24]
        replacements = proposal.get("supersedes", [])
        with self.db:
            existing = self.db.execute(
                "SELECT id,status,version,payload FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            if existing:
                if existing['status'] == 'pending_review' and distinct and not similar:
                    self.db.execute(
                        'UPDATE memories SET status=?,version=version+1,payload=? WHERE id=?',
                        (status, dump(value), memory_id),
                    )
                    self.trace(now, scope, 'memory_clarified', {'id': memory_id, 'status': status})
                    return {'id': memory_id, 'status': status, 'version': existing['version'] + 1}
                previous = json.loads(existing['payload'])
                return {
                    "id": memory_id,
                    "status": "duplicate",
                    "version": existing['version'],
                    **(
                        {'possible_duplicates': previous['possible_duplicates']}
                        if existing['status'] == 'pending_review'
                        else {}
                    ),
                }
            for replacement in replacements:
                old = self.db.execute(
                    "SELECT * FROM memories WHERE id=? AND scope=?", (replacement["id"], scope.key)
                ).fetchone()
                if (
                    not old
                    or old["version"] != replacement.get("version")
                    or old["status"] != "active"
                ):
                    status = "candidate"
                elif set(json.loads(old["payload"])["subject_ids"]) != set(subjects):
                    raise ValueError("Cannot supersede another subject's memory")
                else:
                    old_payload = json.loads(old['payload'])
                    barrier = old_payload.get('manual_barrier')
                    if barrier:
                        if not direct or any(
                            e.seq <= barrier['seq'] or e.time <= barrier['time'] for e in sources
                        ):
                            raise ValueError(
                                'Manual memory can only be replaced by a newer personal statement'
                            )
                        value['manual_barrier'] = barrier
                    old_sources = self.events(scope, ids=old_payload.get('source_message_ids', []))
                    if old_sources and max(e.seq for e in sources) <= max(
                        e.seq for e in old_sources
                    ):
                        status = 'candidate'
            self.db.execute(
                "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?)",
                (memory_id, scope.key, kind, content, status, 1, dump(value), now),
            )
            self.db.executemany(
                "INSERT INTO memory_subjects VALUES (?,?)",
                [(memory_id, subject) for subject in subjects],
            )
            if distinct and status != 'pending_review':
                for pending in self.memories(
                    scope, subjects, statuses=('pending_review',), limit=10000
                ):
                    if pending['content'] == content and same_evidence(
                        evidence, pending.get('evidence', [])
                    ):
                        self.db.execute(
                            "UPDATE memories SET status='clarified',version=version+1 WHERE id=?",
                            (pending['id'],),
                        )
            if status == "active":
                for replacement in replacements:
                    self.db.execute(
                        "UPDATE memories SET status='superseded',version=version+1 WHERE id=?",
                        (replacement["id"],),
                    )
                for old, outcome in resolutions:
                    old_value = json.loads(old['payload'])
                    old_value['resolved_by'] = memory_id
                    self.db.execute(
                        "UPDATE memories SET status=?,version=version+1,payload=? WHERE id=?",
                        (outcome, dump(old_value), old['id']),
                    )
        self.trace(
            now,
            scope,
            "memory_proposal",
            {"id": memory_id, "status": status, "sources": source_ids},
        )
        return {
            "id": memory_id,
            "status": status,
            **({'possible_duplicates': value['possible_duplicates']} if similar else {}),
        }

    def revision(self, scope):
        return self.get('memory_revision:' + scope.key, 0)

    def guards(self, scope):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                'SELECT payload FROM memory_guards WHERE scope=?', (scope.key,)
            )
        ]

    def source_overrides(self, scope, message_id):
        return [
            {
                'memory_id': g['id'],
                'operation': g['operation'],
                'replaced_at': g['replaced_at'],
                'quotes': [
                    r['quote'] for r in g.get('evidence', []) if r['message_id'] == message_id
                ],
            }
            for g in self.guards(scope)
            if message_id in g.get('source_message_ids', [])
        ]

"""记忆管理命令、查询与原子写入；不调用模型。"""

import hashlib
import json
import re
import shlex

from .store import dump


STATUSES = {
    'active',
    'candidate',
    'pending_review',
    'deleted',
    'superseded',
    'confirmed',
    'refuted',
    'merged',
    'clarified',
}
WRITES = {'add', 'edit', 'delete'}
PRIVATE = WRITES | {'history', 'operation'}


def parse_command(text, mentions, user_id):
    # CQ mentions are supplied separately by the adapter, never parsed from text.
    tokens = shlex.split(text, posix=False)
    op = tokens.pop(0).lower() if tokens else 'self'
    if op not in {
        'self',
        'list',
        'search',
        'show',
        'add',
        'edit',
        'delete',
        'history',
        'operation',
    }:
        raise ValueError('未知子命令；发送 /um help 查看帮助')
    request = {
        'op': op,
        'subjects': list(dict.fromkeys(map(str, mentions))),
        'page': 1,
        'status': 'active',
    }
    plain = []
    while tokens:
        token = tokens.pop(0)
        if token in ('--page', '--status', '--kind'):
            allowed = {
                '--page': {'list', 'search', 'history'},
                '--status': {'list', 'search'},
                '--kind': {'add', 'edit'},
            }
            if op not in allowed[token]:
                raise ValueError(f'{token} 不适用于 {op}')
            if not tokens:
                raise ValueError(f'{token} 缺少参数')
            request[token[2:]] = tokens.pop(0)
        elif token.startswith('--'):
            raise ValueError(f'未知参数 {token}')
        else:
            plain.append(token)
    request['page'] = int(request['page'])
    if request['page'] < 1 or request['page'] > 100000:
        raise ValueError('页码必须为正整数')
    if request['status'] not in STATUSES | {'all'}:
        raise ValueError('未知记忆状态')
    if 'kind' in request and request['kind'] not in ('fact', 'event', 'impression'):
        raise ValueError('类型应为 fact、event 或 impression')
    if op == 'self':
        request.update(op='list', subjects=request['subjects'] or [str(user_id)])
    if op in ('show', 'edit', 'delete', 'history', 'operation'):
        if not plain:
            raise ValueError('缺少 ID；编辑和删除需要 ID@版本')
        reference = plain.pop(0)
        if op in ('edit', 'delete'):
            match = re.fullmatch(r'([a-f0-9]{24})@([1-9][0-9]*)', reference)
            if not match:
                raise ValueError('请复制列表中的 ID@版本')
            request.update(id=match[1], version=int(match[2]))
        else:
            request['id'] = reference
    content = ' '.join(plain).strip()
    if op in ('add', 'edit', 'search'):
        if not content or len(content) > 2000:
            raise ValueError('内容不能为空且不得超过2000字')
        request['query' if op == 'search' else 'content'] = content
    elif content:
        raise ValueError('多余参数；发送 /um help 查看帮助')
    if op == 'add' and not request['subjects']:
        raise ValueError('新增记忆必须 @目标用户')
    if len(request['subjects']) > 8:
        raise ValueError('一条记忆最多包含8个主体')
    return request


def requires_admin(request):
    return request['op'] in PRIVATE or request.get('status', 'active') != 'active'


def operation_id(scope, actor, message_id):
    return hashlib.sha256(dump([scope.key, str(actor), str(message_id)]).encode()).hexdigest()[:24]


def memory_value(row):
    return {
        **json.loads(row['payload']),
        **{k: row[k] for k in ('id', 'kind', 'content', 'status', 'version')},
    }


class MemoryManagement:
    def __init__(self, store):
        self.store = store

    def record(self, scope, mid):
        row = self.store.db.execute(
            'SELECT * FROM memories WHERE scope=? AND id=?', (scope.key, mid)
        ).fetchone()
        if not row:
            raise ValueError('本群没有此记忆')
        return memory_value(row)

    def query(self, scope, request, admin):
        op = request['op']
        if requires_admin(request) and not admin:
            raise PermissionError('此操作需要本群群主、管理员或超级管理权限')
        if op == 'operation':
            row = self.store.db.execute(
                'SELECT result FROM memory_operations WHERE scope=? AND id=?',
                (scope.key, request['id']),
            ).fetchone()
            return (
                json.loads(row[0]) if row else {'state': 'not_found', 'operation_id': request['id']}
            )
        if op in ('show', 'history'):
            record = self.record(scope, request['id'])
            if not admin and record['status'] != 'active':
                raise PermissionError('普通群员只能查询有效记忆')
            if op == 'show':
                return {'records': [record]}
            rows = self.store.db.execute(
                'SELECT * FROM memory_history WHERE scope=? AND id=? ORDER BY version DESC LIMIT 10 OFFSET ?',
                (scope.key, request['id'], (request.get('page', 1) - 1) * 10),
            ).fetchall()
            return {
                'records': [
                    {
                        **memory_value(row),
                        'changed_at': row['changed_at'],
                        'audit': json.loads(row['audit']),
                    }
                    for row in rows
                ],
                'page': request.get('page', 1),
            }
        if op not in ('list', 'search'):
            raise ValueError('Unknown memory query')
        status = request.get('status', 'active')
        if status not in STATUSES | {'all'}:
            raise ValueError('Invalid status')
        where, args = ['m.scope=?'], [scope.key]
        if status != 'all':
            where.append('m.status=?')
            args.append(status)
        subjects = request.get('subjects', [])
        if subjects:
            where.append(
                'EXISTS (SELECT 1 FROM memory_subjects s WHERE s.memory_id=m.id AND s.subject IN ('
                + ','.join('?' for _ in subjects)
                + '))'
            )
            args.extend(subjects)
        if op == 'search':
            where.append('instr(lower(m.content),lower(?))>0')
            args.append(request['query'])
        sql = ' FROM memories m WHERE ' + ' AND '.join(where)
        page = int(request.get('page', 1))
        if page < 1:
            raise ValueError('Invalid page')
        total = self.store.db.execute('SELECT COUNT(*)' + sql, args).fetchone()[0]
        rows = self.store.db.execute(
            'SELECT m.*' + sql + ' ORDER BY m.time DESC,m.id LIMIT 10 OFFSET ?',
            [*args, (page - 1) * 10],
        ).fetchall()
        return {'records': [memory_value(row) for row in rows], 'page': page, 'total': total}

    def mutate(self, scope, request, actor, message_id, now):
        """同一事务保存当前条目、审计历史、幂等结果和上下文失效标记。"""
        store, op = self.store, request['op']
        oid = operation_id(scope, actor, message_id)
        encoded = dump({'request': request, 'actor': actor, 'message_id': message_id})
        existing = store.db.execute(
            'SELECT request,result FROM memory_operations WHERE id=? AND scope=?', (oid, scope.key)
        ).fetchone()
        if existing:
            if existing['request'] != encoded:
                raise ValueError('同一命令消息的操作内容发生变化')
            return json.loads(existing['result'])
        if op not in WRITES:
            raise ValueError('Invalid memory mutation')
        old = self.record(scope, request['id']) if op != 'add' else None
        if old and (
            old['version'] != request['version']
            or old['status'] not in ('active', 'candidate', 'pending_review')
        ):
            raise ValueError(
                f"条目已变化或只读；当前 {old['id']}@{old['version']} [{old['status']}]"
            )
        subjects = request.get('subjects') or (old or {}).get('subject_ids', [])
        kind = request.get('kind', (old or {}).get('kind', 'fact'))
        content = request.get('content', (old or {}).get('content', '')).strip()
        if (
            not subjects
            or len(subjects) > 8
            or kind not in ('fact', 'event', 'impression')
            or not content
            or len(content) > 2000
        ):
            raise ValueError('Invalid memory content, subjects or kind')
        mid = old['id'] if old else hashlib.sha256(('manual:' + oid).encode()).hexdigest()[:24]
        version = old['version'] + 1 if old else 1
        seq = store.db.execute(
            'SELECT COALESCE(MAX(seq),0) FROM events WHERE scope=?', (scope.key,)
        ).fetchone()[0]
        # 同时记录入库顺序和消息时间，避免迟到旧消息越过人工修改的截止点。
        payload = {
            'bot_id': scope.bot_id,
            'group_id': scope.group_id,
            'subject_ids': subjects,
            'source_message_ids': [],
            'speaker_ids': [],
            'evidence': [],
            'evidence_type': 'manual',
            'created_at': (old or {}).get('created_at', now),
            'manual_barrier': {'seq': seq, 'time': now},
            'manual': {
                'actor_id': str(actor),
                'message_id': str(message_id),
                'time': now,
                'operation_id': oid,
            },
        }
        status = 'deleted' if op == 'delete' else 'active'
        revision = store.revision(scope) + 1
        result = {
            'state': 'applied',
            'operation_id': oid,
            'id': mid,
            'version': version,
            'status': status,
            'revision': revision,
        }
        store.audit = {
            'origin': 'manual',
            'actor_id': str(actor),
            'message_id': str(message_id),
            'operation_id': oid,
            'time': now,
            'op': op,
        }
        try:
            with store.db:
                # memories 的触发器在本事务中写入版本历史；任何一步失败均回滚。
                if old:
                    # Keep exact revoked fragments and the old content outside ordinary recall.
                    store.db.execute(
                        'INSERT INTO memory_guards(scope,memory_id,payload) VALUES (?,?,?)',
                        (scope.key, mid, dump({**old, 'replaced_at': now, 'operation': op})),
                    )
                    store.db.execute(
                        'UPDATE memories SET kind=?,content=?,status=?,version=?,payload=?,time=? WHERE id=? AND scope=?',
                        (kind, content, status, version, dump(payload), now, mid, scope.key),
                    )
                    store.db.execute('DELETE FROM memory_subjects WHERE memory_id=?', (mid,))
                else:
                    store.db.execute(
                        'INSERT INTO memories VALUES (?,?,?,?,?,?,?,?)',
                        (mid, scope.key, kind, content, status, version, dump(payload), now),
                    )
                store.db.executemany(
                    'INSERT INTO memory_subjects VALUES (?,?)',
                    [(mid, str(uid)) for uid in subjects],
                )
                state = store.state(scope)
                state.update(context=[], visible_sources=[], memory_revision=revision)
                for key, value in [
                    ('memory_revision:' + scope.key, revision),
                    ('state:' + scope.key, state),
                    ('retired_context:' + scope.key, []),
                    ('index_dirty:' + scope.key, True),
                ]:
                    store.db.execute(
                        'INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, dump(value)),
                    )
                store.db.execute(
                    'INSERT INTO memory_operations VALUES (?,?,?,?)',
                    (oid, scope.key, encoded, dump(result)),
                )
                store.db.execute(
                    'INSERT INTO trace(time,scope,kind,payload) VALUES (?,?,?,?)',
                    (now, scope.key, 'memory_manual', dump({**store.audit, **result})),
                )
        finally:
            store.audit = {'origin': 'model'}
        return result


def format_result(result):
    if 'records' not in result:
        if result.get('state') == 'not_found':
            return '尚未查到操作结果：' + result['operation_id']
        return f"已执行：{result['id']}@{result['version']} [{result['status']}]\n操作ID：{result['operation_id']}"
    lines = []
    for item in result['records']:
        lines.append(
            f"{item['id']}@{item['version']} [{item['kind']}/{item['status']}]\n主体：{', '.join(item['subject_ids'])}\n{item['content']}"
        )
        if item.get('manual'):
            value = item['manual']
            lines.append(
                f"人工操作：{value['actor_id']}；消息 {value['message_id']}；时间 {value['time']}"
            )
        for evidence in item.get('evidence', []):
            lines.append(f"来源 {evidence['message_id']}：{evidence['quote']}")
        if item.get('audit'):
            lines.append('版本操作：' + dump(item['audit']))
    if 'page' in result:
        lines.append(
            f"第 {result['page']} 页" + (f"，共 {result['total']} 条" if 'total' in result else '')
        )
    return '\n\n'.join(lines) if result['records'] else '没有符合条件的记忆'

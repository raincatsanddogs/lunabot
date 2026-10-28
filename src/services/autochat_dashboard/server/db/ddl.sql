-- LunaBot Autochat Dashboard Sidecar Database DDL
-- 针对 SQLite WAL 模式优化，所有表结构具备幂等性 (IF NOT EXISTS)

-- 1. 全局元数据与同步水位线表
CREATE TABLE IF NOT EXISTS dash_global_kv (
    key         TEXT PRIMARY KEY,
    value_int   INTEGER NOT NULL DEFAULT 0,
    value_text  TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL
);

-- 初始化默认键值：全局总开关 (1=运行, 0=熔断暂停) 与 配置水位线
INSERT OR IGNORE INTO dash_global_kv (key, value_int, value_text, updated_at) VALUES
('global_autochat_enabled', 1, 'true', 0),
('config_watermark', 1, '', 0),
('llm_provider_config', 0, '{"base_url":"","model":"","api_key_encrypted":""}', 0);

-- 2. 一次性登录票据表 (Chat-to-Web OTT Flow)
CREATE TABLE IF NOT EXISTS dash_auth_tickets (
    ticket_hash     TEXT PRIMARY KEY,          -- SHA-256(raw_ticket)
    user_id         TEXT NOT NULL,             -- 申请者 QQ 号
    role            TEXT NOT NULL,             -- 'superuser' | 'group_admin'
    allowed_groups  TEXT NOT NULL DEFAULT '[]',-- JSON 数组，如 ["123456","789012"]，superuser 为 ["*"]
    allowed_scopes  TEXT NOT NULL DEFAULT '[]',-- JSON 数组，如 ["bot:123456"]，精确匹配 scope
    created_at      INTEGER NOT NULL,          -- Unix 秒
    expires_at      INTEGER NOT NULL           -- Unix 秒 (created_at + 180)
);
CREATE INDEX IF NOT EXISTS idx_dash_tickets_expires ON dash_auth_tickets(expires_at);

-- 3. 分群 Autochat 策略与人设配置表 (直接 1:1 对齐 LunaBot 原生引擎字段)
CREATE TABLE IF NOT EXISTS dash_group_config (
    scope               TEXT PRIMARY KEY,              -- 'bot_id:group_id' 精确主键，100% 命中索引
    group_id            TEXT NOT NULL,                 -- QQ 群号 (方便反查)
    group_name_cache    TEXT NOT NULL DEFAULT '',      -- 群名称缓存，减少 API 频繁查询
    enabled             INTEGER NOT NULL DEFAULT 1,    -- 0: 禁用, 1: 启用
    ambient_p           REAL NOT NULL DEFAULT 0.04,    -- 基础闲聊随缘唤醒概率 (0.00 ~ 1.00)
    followup_p          REAL NOT NULL DEFAULT 0.85,    -- 互动跟进唤醒概率 (0.00 ~ 1.00)
    cooldown_sec        INTEGER NOT NULL DEFAULT 0,    -- 连续回复冷却秒数
    trigger_keywords    TEXT NOT NULL DEFAULT '{}',    -- JSON 字典: {"关键词": 0.2, "阻断词": -1.0}
    persona             TEXT NOT NULL DEFAULT '',      -- 分群专用人设 System Prompt
    blacklist_user_ids  TEXT NOT NULL DEFAULT '[]',    -- 忽略的群成员 QQ 号 JSON 数组
    updated_at          INTEGER NOT NULL               -- Unix 毫秒时间戳 (参与 ETag 与水位线计算)
);
CREATE INDEX IF NOT EXISTS idx_dash_group_gid ON dash_group_config(group_id);
CREATE INDEX IF NOT EXISTS idx_dash_group_updated ON dash_group_config(updated_at);

-- 4. 实时决策审计与 Token 账单表
CREATE TABLE IF NOT EXISTS dash_audit_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scope               TEXT NOT NULL,                 -- 'bot_id:group_id'
    group_id            TEXT NOT NULL,                 -- QQ 群号
    trigger_user_id     TEXT NOT NULL,                 -- 触发人 QQ 号
    trigger_user_name   TEXT NOT NULL DEFAULT '',      -- 触发人昵称
    decision            TEXT NOT NULL,                 -- 'replied' | 'silent' | 'cooldown' | 'blocked' | 'error'
    probability         REAL NOT NULL,                 -- 综合计算出的最终触发概率
    draw                REAL NOT NULL DEFAULT 0.0,     -- 随机抽样采样值 (0.000 ~ 1.000)
    details             TEXT NOT NULL DEFAULT '{}',    -- JSON: {"base_probability":0.04, "matched_keywords":{}, "reason":"ambient"}
    context_digest      TEXT NOT NULL DEFAULT '',      -- 触发当下的最后 1~2 句文本摘要 (图片已被替换为 [[IMG:描述]])
    bot_reply           TEXT NOT NULL DEFAULT '',      -- 生成的回复内容 (未回复则为空字符串)
    model_used          TEXT NOT NULL DEFAULT '',
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    latency_ms          INTEGER NOT NULL DEFAULT 0,
    created_at          INTEGER NOT NULL               -- Unix 秒级时间戳
);
CREATE INDEX IF NOT EXISTS idx_dash_audit_scope_id ON dash_audit_logs(scope, id DESC);
CREATE INDEX IF NOT EXISTS idx_dash_audit_group_id ON dash_audit_logs(group_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_dash_audit_created ON dash_audit_logs(created_at);

-- 5. 原生 Autochat 基础数据表支持 (IF NOT EXISTS，保证与原生 Store 兼容)
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    message_id TEXT NOT NULL,
    speaker TEXT NOT NULL,
    time REAL NOT NULL,
    payload TEXT NOT NULL,
    handled INTEGER NOT NULL DEFAULT 0,
    received REAL NOT NULL,
    UNIQUE(scope, message_id)
);
CREATE INDEX IF NOT EXISTS event_scope ON events(scope, seq);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    time REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_subjects (
    memory_id TEXT REFERENCES memories(id),
    subject TEXT NOT NULL,
    PRIMARY KEY(memory_id, subject)
);

CREATE TABLE IF NOT EXISTS memory_history (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    changed_at REAL NOT NULL,
    audit TEXT NOT NULL,
    PRIMARY KEY(id, version)
);
CREATE INDEX IF NOT EXISTS idx_memory_history_scope_id ON memory_history(scope, id);

CREATE TABLE IF NOT EXISTS memory_guards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    created REAL NOT NULL,
    last_used REAL NOT NULL,
    available INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stickers (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_sent REAL,
    UNIQUE(scope, asset_id)
);

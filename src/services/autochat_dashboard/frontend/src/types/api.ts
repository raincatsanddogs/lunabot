export interface SessionUser {
  user_id: string;
  role: 'superuser' | 'group_admin';
  allowed_groups: string[];
  allowed_scopes: string[];
  token?: string;
}

export interface GroupSummary {
  scope: string;
  group_id: string;
  group_name: string;
  enabled: boolean;
  ambient_p: number;
  updated_at: number;
}

export interface GroupConfigDetail {
  scope: string;
  group_id: string;
  group_name_cache: string;
  enabled: boolean;
  ambient_p: number;
  followup_p: number;
  cooldown_sec: number;
  trigger_keywords: Record<string, number>;
  persona: string;
  blacklist_user_ids: string[];
  updated_at: number;
}

export interface GroupConfigUpdate {
  group_name_cache?: string;
  enabled?: boolean;
  ambient_p?: number;
  followup_p?: number;
  cooldown_sec?: number;
  trigger_keywords?: Record<string, number>;
  persona?: string;
  blacklist_user_ids?: string[];
}

export interface OverviewStats {
  today_total_tokens: number;
  today_prompt_tokens: number;
  today_completion_tokens: number;
  today_replied_count: number;
  today_silent_count: number;
  groups_count: number;
  active_groups_count: number;
}

export interface BootstrapData {
  user: SessionUser;
  global_enabled: boolean;
  config_watermark: number;
  stats: OverviewStats;
  groups: GroupSummary[];
}

export interface AuditLogItem {
  id: number;
  scope: string;
  group_id: string;
  trigger_user_id: string;
  trigger_user_name: string;
  decision: 'replied' | 'silent' | 'cooldown' | 'blocked' | 'error';
  probability: number;
  draw: number;
  details: Record<string, any>;
  context_digest: string;
  bot_reply: string;
  model_used: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  created_at: number;
}

export interface MemoryItem {
  id: string;
  scope: string;
  kind: 'fact' | 'event' | 'impression';
  content: string;
  status: string;
  version: number;
  time: number;
  subject_ids: string[];
  evidence: Array<{ message_id: string; quote: string }>;
  evidence_type: string;
  created_at: number;
}

export interface MemoryCursorResponse {
  items: MemoryItem[];
  next_cursor?: number | null;
  has_more?: boolean;
}

export interface StickerItem {
  id: string;
  scope: string;
  asset_id: string;
  status: string;
  version: number;
  last_sent?: number;
  mime: string;
  size: number;
  description: string;
  text: string;
  intents: string[];
  tone: string[];
  avoid_contexts: string[];
  persona_tags: string[];
}

export interface StickerCursorResponse {
  items: StickerItem[];
  next_cursor?: string | null;
  has_more?: boolean;
}

export interface SandboxEvalResult {
  will_trigger: boolean;
  final_probability: number;
  base_probability: number;
  reason: string;
  matched_keywords: Record<string, number>;
  hard_blocked: boolean;
  blacklisted: boolean;
  explanation: string;
}

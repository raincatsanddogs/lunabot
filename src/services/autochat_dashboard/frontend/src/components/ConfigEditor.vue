<script setup lang="ts">
import { ref, watch } from 'vue';
import { useDashboardState } from '../composables/useDashboardState';
import { toast } from '../composables/useFeedback';
import { apiFetch } from '../utils/http';
import type { SandboxEvalResult } from '../types/api';

const { currentConfig, savingConfig, saveConfig, currentGroupId } = useDashboardState();

// Local edit buffer
const form = ref({
  group_name_cache: '',
  enabled: true,
  ambient_p: 0.04,
  followup_p: 0.85,
  cooldown_sec: 0,
  persona: '',
  trigger_keywords: {} as Record<string, number>,
  blacklist_user_ids: [] as string[],
});

// Keywords editing helpers
const newKw = ref('');
const newDelta = ref(0.2);

const blacklistInput = ref('');

// Sandbox simulation
const mockText = ref('今天吃啥好呀');
const mockUserId = ref('10001');
const mockAt = ref(false);
const mockFollowup = ref(false);
const sandboxResult = ref<SandboxEvalResult | null>(null);
const evalLoading = ref(false);

watch(
  currentConfig,
  (cfg) => {
    if (!cfg) return;
    form.value = {
      group_name_cache: cfg.group_name_cache,
      enabled: cfg.enabled,
      ambient_p: cfg.ambient_p,
      followup_p: cfg.followup_p,
      cooldown_sec: cfg.cooldown_sec,
      persona: cfg.persona,
      trigger_keywords: { ...cfg.trigger_keywords },
      blacklist_user_ids: [...cfg.blacklist_user_ids],
    };
    blacklistInput.value = cfg.blacklist_user_ids.join(', ');
    sandboxResult.value = null;
  },
  { immediate: true }
);

const addKeyword = () => {
  const kw = newKw.value.trim();
  if (!kw) return;
  form.value.trigger_keywords[kw] = Number(newDelta.value);
  newKw.value = '';
  newDelta.value = 0.2;
};

const removeKeyword = (kw: string) => {
  delete form.value.trigger_keywords[kw];
};

const handleSave = async () => {
  // Parse blacklist
  const list = blacklistInput.value
    .split(/[\s,，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  form.value.blacklist_user_ids = list;

  try {
    await saveConfig(form.value);
    toast.success('分群人设与原生策略保存成功，已同步更新水位线！');
  } catch (err: any) {
    toast.error(err.message || '保存策略失败');
  }
};

const runSandbox = async () => {
  if (!currentGroupId.value || !mockText.value.trim()) return;
  evalLoading.value = true;
  try {
    const res = await apiFetch<SandboxEvalResult>(
      `/api/v1/groups/${currentGroupId.value}/sandbox-eval`,
      {
        method: 'POST',
        body: JSON.stringify({
          mock_text: mockText.value,
          mock_user_id: mockUserId.value,
          is_at_bot: mockAt.value,
          is_followup: mockFollowup.value,
        }),
      }
    );
    sandboxResult.value = res;
  } catch (err: any) {
    toast.error(err.message || '沙盒测算失败');
  } finally {
    evalLoading.value = false;
  }
};
</script>

<template>
  <div v-if="currentConfig" class="flex-1 overflow-y-auto w-full">
    <div class="max-w-4xl mx-auto p-3 sm:p-4 md:p-6 space-y-4 sm:space-y-6">
      <!-- Header info & Enable switch -->
      <div class="card-panel flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 class="text-base font-bold text-slate-800 flex flex-wrap items-center gap-2">
            <span>{{ form.group_name_cache || '未命名群聊' }}</span>
            <span class="text-xs font-mono text-slate-400">({{ currentConfig.group_id }})</span>
          </h2>
          <div class="text-xs font-mono text-slate-500 mt-1">
            Exact Scope: <span class="text-sky-600 font-semibold">{{ currentConfig.scope }}</span>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2 text-xs font-medium text-slate-700 cursor-pointer select-none">
            <input type="checkbox" v-model="form.enabled" class="accent-sky-600 w-4 h-4 cursor-pointer" />
            <span>{{ form.enabled ? '本群接话启用' : '本群已静默' }}</span>
          </label>
          <button @click="handleSave" :disabled="savingConfig" class="btn-primary whitespace-nowrap">
            {{ savingConfig ? '正在保存...' : '保存策略' }}
          </button>
        </div>
      </div>

      <!-- 1:1 Native Probability Sliders -->
      <div class="card-panel space-y-4">
        <h3 class="text-xs font-bold uppercase tracking-wider text-slate-500 border-b border-slate-200 pb-2">
          🎲 原生唤醒概率与冷却参数 (1:1 Native Trigger)
        </h3>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6">
          <!-- Ambient Probability (基础闲聊接话) -->
          <div class="space-y-1.5">
            <div class="flex justify-between text-xs">
              <span class="font-medium text-slate-700">环境接话概率 (ambient_p)</span>
              <span class="font-mono text-sky-600 font-bold">{{ (form.ambient_p * 100).toFixed(0) }}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              v-model.number="form.ambient_p"
              class="w-full cursor-pointer"
            />
            <div class="text-[11px] text-slate-400">群友日常聊天闲聊时，Bot 随缘参与的被动唤醒概率（原生配置基准）。</div>
          </div>

          <!-- Followup Probability (互动跟进接话) -->
          <div class="space-y-1.5">
            <div class="flex justify-between text-xs">
              <span class="font-medium text-slate-700">连贯跟进概率 (followup_p)</span>
              <span class="font-mono text-indigo-600 font-bold">{{ (form.followup_p * 100).toFixed(0) }}%</span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              v-model.number="form.followup_p"
              class="w-full cursor-pointer"
            />
            <div class="text-[11px] text-slate-400">刚与 Bot 互动过的群友继续发言时，处于专注窗口内的跟进概率（原生配置基准）。</div>
          </div>

          <!-- Cooldown Seconds -->
          <div class="space-y-1.5">
            <label class="block text-xs font-medium text-slate-700">连续发言冷却秒数 (cooldown_sec)</label>
            <input
              type="number"
              min="0"
              max="3600"
              v-model.number="form.cooldown_sec"
              class="input-base w-full text-xs font-mono"
            />
            <div class="text-[11px] text-slate-400">触发一次回复后的硬冷却保护时间（0 为使用引擎默认防刷屏机制）。</div>
          </div>

          <!-- Group Name Alias -->
          <div class="space-y-1.5">
            <label class="block text-xs font-medium text-slate-700">群名称备注缓存</label>
            <input
              type="text"
              v-model="form.group_name_cache"
              placeholder="自定义群显示名称"
              class="input-base w-full text-xs"
            />
            <div class="text-[11px] text-slate-400">看板侧群列表快速识别备注。</div>
          </div>
        </div>
      </div>

      <!-- Trigger Keywords Table -->
      <div class="card-panel space-y-3">
        <div class="flex flex-wrap items-center justify-between border-b border-slate-200 pb-2 gap-1">
          <h3 class="text-xs font-bold uppercase tracking-wider text-slate-500">
            🎯 关键词权重与强阻断规则 (trigger_keywords)
          </h3>
          <span class="text-[11px] text-slate-400">正数提高概率，-1.0 强阻断静默</span>
        </div>

        <!-- Add keyword input -->
        <div class="flex flex-wrap sm:flex-nowrap gap-2">
          <input
            type="text"
            v-model="newKw"
            placeholder="输入关键词（如：音游、猫猫、闭嘴）"
            class="input-base flex-1 min-w-[140px] text-xs"
          />
          <select v-model.number="newDelta" class="input-base text-xs w-full sm:w-36 font-mono">
            <option :value="0.1">+0.1 (微增)</option>
            <option :value="0.2">+0.2 (推荐)</option>
            <option :value="0.4">+0.4 (显著)</option>
            <option :value="0.8">+0.8 (极高)</option>
            <option :value="-0.5">-0.5 (抑制)</option>
            <option :value="-1.0">-1.0 (强阻断)</option>
          </select>
          <button @click="addKeyword" class="btn-secondary w-full sm:w-auto shrink-0 whitespace-nowrap">添加词条</button>
        </div>

        <!-- Keyword pills -->
        <div class="flex flex-wrap gap-2 pt-1">
          <div
            v-for="(delta, kw) in form.trigger_keywords"
            :key="kw"
            class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono border"
            :class="
              delta <= -1.0
                ? 'bg-rose-50 text-rose-700 border-rose-200'
                : delta > 0
                ? 'bg-sky-50 text-sky-700 border-sky-200'
                : 'bg-slate-100 text-slate-700 border-slate-200'
            "
          >
            <span class="font-sans font-medium">{{ kw }}:</span>
            <span class="font-bold">{{ delta > 0 ? `+${delta}` : delta }}</span>
            <button @click="removeKeyword(kw)" class="ml-1 text-slate-400 hover:text-rose-600 font-bold transition">×</button>
          </div>
          <div v-if="Object.keys(form.trigger_keywords).length === 0" class="text-xs text-slate-400 italic py-1">
            暂无特殊关键词加权规则
          </div>
        </div>
      </div>

      <!-- Group Persona System Prompt -->
      <div class="card-panel space-y-2">
        <div class="flex flex-wrap items-center justify-between border-b border-slate-200 pb-2 gap-1">
          <h3 class="text-xs font-bold uppercase tracking-wider text-slate-500">
            🎭 分群专用人设 System Prompt (personas[group_id])
          </h3>
          <span class="text-[11px] text-slate-400">留空则继承全局默认人设</span>
        </div>
        <textarea
          v-model="form.persona"
          rows="5"
          placeholder="自定义分群专用人设。未配置时自动继承原生全局默认人设（如东云绘名设定）..."
          class="input-base w-full text-xs font-mono leading-relaxed"
        ></textarea>
      </div>

      <!-- Blacklist User IDs -->
      <div class="card-panel space-y-2">
        <h3 class="text-xs font-bold uppercase tracking-wider text-slate-500 border-b border-slate-200 pb-2">
          🚫 群内忽略成员黑名单 (blacklist_user_ids)
        </h3>
        <input
          type="text"
          v-model="blacklistInput"
          placeholder="输入需忽略的成员 QQ 号，多个使用空格或逗号分隔..."
          class="input-base w-full text-xs font-mono"
        />
        <div class="text-[11px] text-slate-400">黑名单内的成员发送的消息将被静默处理，不触发任何接话与跟进。</div>
      </div>

      <!-- Sandbox Simulation Tester -->
      <div class="bg-sky-50/40 border border-sky-200 shadow-sm rounded-xl p-4 sm:p-5 space-y-4">
        <div class="flex flex-wrap items-center justify-between border-b border-sky-200/80 pb-2 gap-1">
          <h3 class="text-xs font-bold uppercase tracking-wider text-sky-800 flex items-center gap-1.5">
            <span>🧪 意愿计算沙盒调试器 (Sandbox Eval)</span>
          </h3>
          <span class="text-[10px] text-slate-500 font-mono">内存运行 • 0 扣费 • 0 发信风控</span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <div class="sm:col-span-2">
            <input v-model="mockText" type="text" placeholder="输入模拟群友发言..." class="input-base w-full" />
          </div>
          <div>
            <input v-model="mockUserId" type="text" placeholder="模拟发送者QQ" class="input-base w-full font-mono" />
          </div>
          <div class="flex items-center gap-3 px-1 py-1">
            <label class="flex items-center gap-1 cursor-pointer text-slate-700 select-none">
              <input type="checkbox" v-model="mockAt" class="accent-sky-600 w-4 h-4 cursor-pointer" />
              <span>@Bot</span>
            </label>
            <label class="flex items-center gap-1 cursor-pointer text-slate-700 select-none">
              <input type="checkbox" v-model="mockFollowup" class="accent-indigo-600 w-4 h-4 cursor-pointer" />
              <span>跟进中</span>
            </label>
          </div>
        </div>

        <div class="flex justify-end">
          <button @click="runSandbox" :disabled="evalLoading" class="btn-primary w-full sm:w-auto">
            {{ evalLoading ? '正在测算...' : '运行沙盒测算' }}
          </button>
        </div>

        <!-- Sandbox Result Display -->
        <div v-if="sandboxResult" class="p-3.5 bg-white rounded-lg border border-sky-200 text-xs space-y-2 shadow-xs">
          <div class="flex flex-wrap items-center justify-between gap-1">
            <div class="flex flex-wrap items-center gap-2">
              <span
                class="px-2 py-0.5 rounded font-bold text-xs border"
                :class="
                  sandboxResult.will_trigger
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-slate-100 text-slate-600 border-slate-200'
                "
              >
                {{ sandboxResult.will_trigger ? '⚡ 判定触发接话' : '💤 判定保持静默' }}
              </span>
              <span class="font-mono text-slate-700">
                最终唤醒概率: <strong>{{ (sandboxResult.final_probability * 100).toFixed(0) }}%</strong>
              </span>
            </div>
            <span class="text-[11px] text-slate-500 font-mono">
              基准: {{ sandboxResult.base_probability }} ({{ sandboxResult.reason }})
            </span>
          </div>
          <div class="text-[11px] text-slate-700 font-mono bg-slate-50 border border-slate-100 p-2.5 rounded-lg leading-relaxed break-words">
            {{ sandboxResult.explanation }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

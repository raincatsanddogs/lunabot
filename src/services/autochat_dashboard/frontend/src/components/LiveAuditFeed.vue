<script setup lang="ts">
import { ref } from 'vue';
import { useAuditStream } from '../composables/useAuditStream';
import { formatSmartDateTime, formatFullDateTime } from '../utils/format';
import { parseDigestBadges, getQQUserAvatar } from '../utils/qq_media';

const props = defineProps<{
  groupId: string;
}>();

const { auditLogs, sseOnline, clearLogs } = useAuditStream(ref(props.groupId));
</script>

<template>
  <div class="h-64 border-t border-slate-200 bg-white flex flex-col shrink-0 overflow-hidden select-none">
    <!-- Header -->
    <div class="h-9 px-4 border-b border-slate-200 flex items-center justify-between bg-slate-50 text-xs shrink-0">
      <div class="flex items-center gap-2">
        <span class="font-bold text-slate-800 flex items-center gap-1.5">
          <span>📡 实时触发决策流</span>
        </span>
        <span
          class="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-mono"
          :class="sseOnline ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-slate-100 text-slate-500 border border-slate-200'"
        >
          <span class="w-1.5 h-1.5 rounded-full" :class="sseOnline ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'"></span>
          <span>{{ sseOnline ? '实时推流中' : '休眠断连' }}</span>
        </span>
      </div>

      <div class="flex items-center gap-2">
        <span class="text-[11px] text-slate-400 font-mono">缓冲: {{ auditLogs.length }}/200</span>
        <button
          @click="clearLogs"
          class="text-xs text-slate-500 hover:text-rose-600 transition px-2 py-0.5 rounded hover:bg-slate-200/50"
        >
          清空
        </button>
      </div>
    </div>

    <!-- Feed log stream list -->
    <div class="flex-1 overflow-y-auto divide-y divide-slate-100 p-2 space-y-1 font-mono text-xs">
      <div
        v-for="log in auditLogs"
        :key="log.id"
        class="p-2.5 rounded-lg bg-slate-50/70 hover:bg-slate-100/80 border border-slate-100 transition flex items-start gap-3"
      >
        <!-- User avatar -->
        <img
          :src="getQQUserAvatar(log.trigger_user_id)"
          loading="lazy"
          decoding="async"
          referrerpolicy="no-referrer"
          class="w-6 h-6 rounded-full bg-slate-100 shrink-0 mt-0.5 border border-slate-200"
          @error="($event.target as HTMLElement).style.display = 'none'"
        />

        <div class="flex-1 min-w-0 space-y-1">
          <!-- Top line -->
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span
                class="px-1.5 py-0.5 text-[10px] rounded font-bold uppercase border"
                :class="
                  log.decision === 'replied'
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : log.decision === 'blocked'
                    ? 'bg-rose-50 text-rose-700 border-rose-200'
                    : 'bg-slate-100 text-slate-600 border border-slate-200'
                "
              >
                {{ log.decision }}
              </span>
              <span class="text-slate-800 font-sans font-medium text-xs">{{ log.trigger_user_name || log.trigger_user_id }}</span>
              <span class="text-slate-400 text-[10px]">群: {{ log.group_id }}</span>
            </div>

            <div class="flex items-center gap-2 text-[11px] text-slate-400">
              <span v-if="log.prompt_tokens" class="text-sky-600 font-semibold">{{ log.prompt_tokens + log.completion_tokens }} tok</span>
              <span v-if="log.latency_ms" class="text-slate-500">{{ log.latency_ms }}ms</span>
              <span :title="formatFullDateTime(log.created_at)">{{ formatSmartDateTime(log.created_at) }}</span>
            </div>
          </div>

          <!-- Context digest with multimodal badges [[IMG:描述]] -->
          <div class="text-slate-700 font-sans text-xs flex flex-wrap items-center gap-1">
            <template v-for="(token, idx) in parseDigestBadges(log.context_digest)" :key="idx">
              <span v-if="token.type === 'text'">{{ token.content }}</span>
              <span
                v-else-if="token.type === 'image_badge'"
                class="inline-flex items-center gap-1 px-1.5 py-0.5 bg-sky-50 text-sky-700 border border-sky-200 rounded text-[10px] font-mono"
              >
                📷 {{ token.content }}
              </span>
            </template>
          </div>

          <!-- Bot Reply if replied -->
          <div v-if="log.bot_reply" class="p-2 bg-emerald-50 border-l-2 border-emerald-500 text-emerald-800 text-xs font-sans rounded-r">
            🤖 {{ log.bot_reply }}
          </div>
        </div>
      </div>

      <div v-if="auditLogs.length === 0" class="text-center text-xs text-slate-400 py-8 font-sans">
        暂无决策流，群聊产生消息后将在此实时浮现...
      </div>
    </div>
  </div>
</template>

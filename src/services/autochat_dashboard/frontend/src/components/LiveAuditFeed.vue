<script setup lang="ts">
import { ref } from 'vue';
import { useAuditStream } from '../composables/useAuditStream';
import { formatRelativeTime } from '../utils/format';
import { parseDigestBadges, getQQUserAvatar } from '../utils/qq_media';

const props = defineProps<{
  groupId: string;
}>();

const { auditLogs, sseOnline, clearLogs } = useAuditStream(ref(props.groupId));
</script>

<template>
  <div class="h-64 border-t border-slate-800 bg-slate-900/60 flex flex-col shrink-0 overflow-hidden select-none">
    <!-- Header -->
    <div class="h-9 px-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90 text-xs shrink-0">
      <div class="flex items-center gap-2">
        <span class="font-bold text-slate-200 flex items-center gap-1.5">
          <span>📡 实时触发决策流</span>
        </span>
        <span
          class="flex items-center gap-1 text-[11px] px-2 py-0.2 rounded-full font-mono"
          :class="sseOnline ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-500'"
        >
          <span class="w-1.5 h-1.5 rounded-full" :class="sseOnline ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'"></span>
          <span>{{ sseOnline ? '实时推流中' : '休眠断连' }}</span>
        </span>
      </div>

      <div class="flex items-center gap-2">
        <span class="text-[11px] text-slate-500 font-mono">缓冲: {{ auditLogs.length }}/200</span>
        <button @click="clearLogs" class="text-slate-400 hover:text-slate-200 text-xs">清空</button>
      </div>
    </div>

    <!-- Feed log stream list -->
    <div class="flex-1 overflow-y-auto divide-y divide-slate-800/40 p-2 space-y-1 font-mono text-xs">
      <div
        v-for="log in auditLogs"
        :key="log.id"
        class="p-2 rounded bg-slate-900/40 hover:bg-slate-800/50 transition flex items-start gap-3"
      >
        <!-- User avatar -->
        <img
          :src="getQQUserAvatar(log.trigger_user_id)"
          loading="lazy"
          decoding="async"
          referrerpolicy="no-referrer"
          class="w-6 h-6 rounded-full bg-slate-800 shrink-0 mt-0.5"
          @error="($event.target as HTMLElement).style.display = 'none'"
        />

        <div class="flex-1 min-w-0 space-y-1">
          <!-- Top line -->
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span
                class="px-1.5 py-0.2 text-[10px] rounded font-bold uppercase"
                :class="
                  log.decision === 'replied'
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                    : log.decision === 'blocked'
                    ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                    : 'bg-slate-800 text-slate-400 border border-slate-700'
                "
              >
                {{ log.decision }}
              </span>
              <span class="text-slate-300 font-sans font-medium text-xs">{{ log.trigger_user_name || log.trigger_user_id }}</span>
              <span class="text-slate-500 text-[10px]">群: {{ log.group_id }}</span>
            </div>

            <div class="flex items-center gap-2 text-[11px] text-slate-500">
              <span v-if="log.prompt_tokens" class="text-sky-400/80">{{ log.prompt_tokens + log.completion_tokens }} tok</span>
              <span v-if="log.latency_ms" class="text-slate-400">{{ log.latency_ms }}ms</span>
              <span>{{ formatRelativeTime(log.created_at) }}</span>
            </div>
          </div>

          <!-- Context digest with multimodal badges [[IMG:描述]] -->
          <div class="text-slate-300 font-sans text-xs flex flex-wrap items-center gap-1">
            <template v-for="(token, idx) in parseDigestBadges(log.context_digest)" :key="idx">
              <span v-if="token.type === 'text'">{{ token.content }}</span>
              <span
                v-else-if="token.type === 'image_badge'"
                class="inline-flex items-center gap-1 px-1.5 py-0.2 bg-sky-500/10 text-sky-300 border border-sky-500/20 rounded text-[10px] font-mono"
              >
                📷 {{ token.content }}
              </span>
            </template>
          </div>

          <!-- Bot Reply if replied -->
          <div v-if="log.bot_reply" class="p-1.5 bg-emerald-950/20 border-l-2 border-emerald-400 text-emerald-200 text-xs font-sans rounded-r">
            🤖 {{ log.bot_reply }}
          </div>
        </div>
      </div>

      <div v-if="auditLogs.length === 0" class="text-center text-xs text-slate-500 py-8 font-sans">
        暂无决策流，群聊产生消息后将在此实时浮现...
      </div>
    </div>
  </div>
</template>

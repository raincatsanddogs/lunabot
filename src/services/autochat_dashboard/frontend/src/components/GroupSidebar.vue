<script setup lang="ts">
import { ref, computed } from 'vue';
import { useDashboardState } from '../composables/useDashboardState';
import { getQQGroupAvatar } from '../utils/qq_media';

const { bootstrap, currentGroupId, selectGroup } = useDashboardState();
const searchQuery = ref('');

const filteredGroups = computed(() => {
  if (!bootstrap.value) return [];
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return bootstrap.value.groups;
  return bootstrap.value.groups.filter(
    (g) => g.group_id.includes(q) || g.group_name.toLowerCase().includes(q)
  );
});
</script>

<template>
  <aside class="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col shrink-0 overflow-hidden select-none">
    <!-- Search filter input -->
    <div class="p-3 border-b border-slate-800">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索群号或群名称..."
        class="input-dark w-full text-xs py-1.5"
      />
    </div>

    <!-- Group list -->
    <div class="flex-1 overflow-y-auto divide-y divide-slate-800/40">
      <div
        v-for="group in filteredGroups"
        :key="group.scope"
        @click="selectGroup(group.group_id)"
        class="p-2.5 flex items-center gap-3 cursor-pointer transition hover:bg-slate-800/60"
        :class="currentGroupId === group.group_id ? 'bg-slate-800 border-l-2 border-sky-400' : ''"
      >
        <!-- Group Avatar via official CDN -->
        <img
          :src="getQQGroupAvatar(group.group_id)"
          loading="lazy"
          decoding="async"
          referrerpolicy="no-referrer"
          class="w-8 h-8 rounded-full bg-slate-800 object-cover shrink-0"
          @error="($event.target as HTMLElement).style.display = 'none'"
        />

        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between">
            <span class="text-xs font-semibold text-slate-200 truncate">{{ group.group_name }}</span>
            <span
              class="w-1.5 h-1.5 rounded-full shrink-0"
              :class="group.enabled ? 'bg-emerald-400' : 'bg-slate-600'"
              :title="group.enabled ? '启用中' : '已禁用'"
            ></span>
          </div>
          <div class="flex items-center justify-between text-[11px] text-slate-400 mt-0.5">
            <span class="font-mono">{{ group.group_id }}</span>
            <span class="font-mono text-[10px] text-sky-400/80">{{ (group.ambient_p * 100).toFixed(0) }}% 随缘</span>
          </div>
        </div>
      </div>

      <div v-if="filteredGroups.length === 0" class="p-6 text-center text-xs text-slate-500">
        无匹配群聊
      </div>
    </div>
  </aside>
</template>

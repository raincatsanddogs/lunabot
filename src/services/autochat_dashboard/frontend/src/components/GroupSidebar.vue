<script setup lang="ts">
import { ref, computed } from 'vue';
import { useDashboardState } from '../composables/useDashboardState';
import { getQQGroupAvatar } from '../utils/qq_media';

const { bootstrap, currentGroupId, selectGroup, sidebarOpen, sidebarCollapsed } = useDashboardState();
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
  <aside
    class="bg-white flex flex-col shrink-0 select-none transition-all duration-300 ease-in-out
      fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] shadow-2xl
      md:static md:inset-auto md:z-auto md:shadow-none md:translate-x-0"
    :class="[
      sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
      sidebarCollapsed
        ? 'md:w-0 md:border-none md:overflow-hidden md:opacity-0 md:pointer-events-none'
        : 'md:w-64 md:border-r md:border-slate-200 md:opacity-100 md:pointer-events-auto'
    ]"
  >
    <!-- Inner container with fixed width to prevent content from reflowing/squishing during collapse transition -->
    <div class="w-72 max-w-[85vw] md:w-64 flex flex-col h-full overflow-hidden shrink-0">
      <!-- Search filter input & Mobile close button -->
      <div class="p-3 border-b border-slate-200 flex items-center gap-2 shrink-0">
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索群号或群名称..."
          class="input-base w-full text-xs py-1.5 flex-1"
        />
        <button
          @click="sidebarOpen = false"
          class="md:hidden p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition shrink-0 cursor-pointer"
          title="关闭抽屉"
          aria-label="Close Drawer"
        >
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Group list -->
      <div class="flex-1 overflow-y-auto divide-y divide-slate-100">
        <div
          v-for="group in filteredGroups"
          :key="group.scope"
          @click="selectGroup(group.group_id)"
          class="p-2.5 flex items-center gap-3 cursor-pointer transition"
          :class="
            currentGroupId === group.group_id
              ? 'bg-sky-50 text-sky-700 font-semibold border-l-2 border-sky-500'
              : 'hover:bg-slate-50 text-slate-700'
          "
        >
          <!-- Group Avatar via official CDN -->
          <img
            :src="getQQGroupAvatar(group.group_id)"
            loading="lazy"
            decoding="async"
            referrerpolicy="no-referrer"
            class="w-8 h-8 rounded-full bg-slate-100 object-cover shrink-0 border border-slate-100"
            @error="($event.target as HTMLElement).style.display = 'none'"
          />

          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between">
              <span
                class="text-xs truncate"
                :class="currentGroupId === group.group_id ? 'text-sky-900 font-bold' : 'font-semibold text-slate-800'"
              >
                {{ group.group_name }}
              </span>
              <span
                class="w-1.5 h-1.5 rounded-full shrink-0"
                :class="group.enabled ? 'bg-emerald-500' : 'bg-slate-300'"
                :title="group.enabled ? '启用中' : '已禁用'"
              ></span>
            </div>
            <div class="flex items-center justify-between text-[11px] mt-0.5">
              <span class="font-mono" :class="currentGroupId === group.group_id ? 'text-sky-700/80' : 'text-slate-400'">
                {{ group.group_id }}
              </span>
              <span class="font-mono text-[10px] text-sky-600 font-medium">
                {{ (group.ambient_p * 100).toFixed(0) }}% 随缘
              </span>
            </div>
          </div>
        </div>

        <div v-if="filteredGroups.length === 0" class="p-6 text-center text-xs text-slate-400">
          无匹配群聊
        </div>
      </div>
    </div>
  </aside>
</template>

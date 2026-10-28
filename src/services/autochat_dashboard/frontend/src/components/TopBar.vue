<script setup lang="ts">
import { useAuth } from '../composables/useAuth';
import { useDashboardState } from '../composables/useDashboardState';
import { formatTokenCount } from '../utils/format';

const { currentUser, logout } = useAuth();
const { bootstrap, toggleGlobalEnabled } = useDashboardState();
</script>

<template>
  <header class="h-14 border-b border-slate-200 bg-white px-4 flex items-center justify-between shrink-0 select-none">
    <!-- Brand -->
    <div class="flex items-center gap-2.5">
      <span class="text-xl">🌙</span>
      <div>
        <div class="flex items-center gap-2">
          <span class="font-bold text-sm text-slate-800">LunaBot Autochat</span>
          <span class="text-[10px] px-1.5 py-0.5 bg-sky-50 text-sky-600 border border-sky-200 rounded font-mono font-medium">Sidecar</span>
        </div>
      </div>
    </div>

    <!-- Center: Global Switch & Telemetry Pill -->
    <div class="flex items-center gap-3">
      <!-- Global Emergency Switch -->
      <button
        v-if="currentUser?.role === 'superuser' && bootstrap"
        @click="toggleGlobalEnabled(!bootstrap.global_enabled)"
        class="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition cursor-pointer select-none active:scale-98"
        :class="bootstrap.global_enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100' : 'bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100'"
      >
        <span class="w-1.5 h-1.5 rounded-full" :class="bootstrap.global_enabled ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"></span>
        <span>{{ bootstrap.global_enabled ? '全局运行中' : '全局已熔断' }}</span>
      </button>

      <!-- Token & Decisions Metrics Pill -->
      <div v-if="bootstrap" class="hidden sm:flex items-center gap-3 px-3 py-1 bg-slate-50 border border-slate-200 rounded-full text-xs font-mono text-slate-600">
        <div>
          <span class="text-slate-400 text-[10px] uppercase mr-1">今日Token:</span>
          <span class="text-sky-600 font-semibold">{{ formatTokenCount(bootstrap.stats.today_total_tokens) }}</span>
        </div>
        <div class="w-px h-3 bg-slate-200"></div>
        <div>
          <span class="text-slate-400 text-[10px] uppercase mr-1">触发:</span>
          <span class="text-emerald-600 font-semibold">{{ bootstrap.stats.today_replied_count }}</span>
          <span class="text-slate-400 text-[10px] ml-1">/ 静默: {{ bootstrap.stats.today_silent_count }}</span>
        </div>
      </div>
    </div>

    <!-- Right: User Identity & Logout -->
    <div class="flex items-center gap-2.5">
      <div v-if="currentUser" class="flex items-center gap-2">
        <span
          class="text-xs px-2 py-0.5 rounded font-mono border"
          :class="currentUser.role === 'superuser' ? 'bg-indigo-50 text-indigo-700 border-indigo-200 font-medium' : 'bg-slate-100 text-slate-700 border-slate-200 font-medium'"
        >
          {{ currentUser.role === 'superuser' ? '超级管理员' : '群管理员' }}
        </span>
        <span class="text-xs font-mono text-slate-500 hidden md:inline">QQ: {{ currentUser.user_id }}</span>
      </div>
      <button @click="logout" class="btn-secondary text-xs py-1 px-2.5 hover:text-rose-600 hover:border-rose-200">
        退出
      </button>
    </div>
  </header>
</template>

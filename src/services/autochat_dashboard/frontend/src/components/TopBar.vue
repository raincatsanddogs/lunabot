<script setup lang="ts">
import { useAuth } from '../composables/useAuth';
import { useDashboardState } from '../composables/useDashboardState';
import { formatTokenCount } from '../utils/format';

const { currentUser, logout } = useAuth();
const { bootstrap, toggleGlobalEnabled } = useDashboardState();
</script>

<template>
  <header class="h-14 border-b border-slate-800 bg-slate-900/90 px-4 flex items-center justify-between backdrop-blur shrink-0 select-none">
    <!-- Brand -->
    <div class="flex items-center gap-2.5">
      <span class="text-xl">🌙</span>
      <div>
        <div class="flex items-center gap-2">
          <span class="font-bold text-sm text-slate-100">LunaBot Autochat</span>
          <span class="text-[10px] px-1.5 py-0.2 bg-sky-500/10 text-sky-400 border border-sky-500/20 rounded font-mono">Sidecar</span>
        </div>
      </div>
    </div>

    <!-- Center: Global Switch & Telemetry Pill -->
    <div class="flex items-center gap-3">
      <!-- Global Emergency Switch -->
      <button
        v-if="currentUser?.role === 'superuser' && bootstrap"
        @click="toggleGlobalEnabled(!bootstrap.global_enabled)"
        class="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition cursor-pointer"
        :class="bootstrap.global_enabled ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/30 hover:bg-rose-500/20'"
      >
        <span class="w-1.5 h-1.5 rounded-full" :class="bootstrap.global_enabled ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'"></span>
        <span>{{ bootstrap.global_enabled ? '全局运行中' : '全局已熔断' }}</span>
      </button>

      <!-- Token & Decisions Metrics Pill -->
      <div v-if="bootstrap" class="hidden sm:flex items-center gap-3 px-3 py-1 bg-slate-800/80 border border-slate-700/60 rounded-full text-xs font-mono text-slate-300">
        <div>
          <span class="text-slate-400 text-[10px] uppercase mr-1">今日Token:</span>
          <span class="text-sky-400 font-semibold">{{ formatTokenCount(bootstrap.stats.today_total_tokens) }}</span>
        </div>
        <div class="w-px h-3 bg-slate-700"></div>
        <div>
          <span class="text-slate-400 text-[10px] uppercase mr-1">触发:</span>
          <span class="text-emerald-400 font-semibold">{{ bootstrap.stats.today_replied_count }}</span>
          <span class="text-slate-400 text-[10px] ml-1">/ 静默: {{ bootstrap.stats.today_silent_count }}</span>
        </div>
      </div>
    </div>

    <!-- Right: User Identity & Logout -->
    <div class="flex items-center gap-2.5">
      <div v-if="currentUser" class="flex items-center gap-2">
        <span class="text-xs px-2 py-0.5 rounded font-mono border"
          :class="currentUser.role === 'superuser' ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30' : 'bg-slate-700 text-slate-300 border-slate-600'">
          {{ currentUser.role === 'superuser' ? '超级管理员' : '群管理员' }}
        </span>
        <span class="text-xs font-mono text-slate-300 hidden md:inline">QQ: {{ currentUser.user_id }}</span>
      </div>
      <button @click="logout" class="btn-secondary text-xs py-1 px-2.5 hover:text-rose-300">
        退出
      </button>
    </div>
  </header>
</template>

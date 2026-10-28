<script setup lang="ts">
import { useAuth } from '../composables/useAuth';
import { useDashboardState } from '../composables/useDashboardState';
import { formatTokenCount } from '../utils/format';

const { currentUser, logout } = useAuth();
const { bootstrap, toggleGlobalEnabled, toggleSidebarOpen, toggleSidebarCollapsed } = useDashboardState();

const handleSidebarToggle = () => {
  if (typeof window !== 'undefined' && window.innerWidth < 768) {
    toggleSidebarOpen();
  } else {
    toggleSidebarCollapsed();
  }
};
</script>

<template>
  <header class="h-14 border-b border-slate-200 bg-white px-2.5 sm:px-4 flex items-center justify-between shrink-0 select-none">
    <!-- Brand & Sidebar Toggle -->
    <div class="flex items-center gap-1.5 sm:gap-2.5 min-w-0">
      <button
        @click="handleSidebarToggle"
        class="p-1.5 -ml-1 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition shrink-0 cursor-pointer select-none"
        title="切换侧边栏"
        aria-label="Toggle Sidebar"
      >
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      <span class="text-lg sm:text-xl shrink-0">🌙</span>

      <div class="flex items-center gap-1.5 truncate">
        <span class="font-bold text-xs sm:text-sm text-slate-800 truncate">LunaBot</span>
        <span class="hidden sm:inline font-bold text-xs sm:text-sm text-slate-800">Autochat</span>
        <span class="text-[10px] px-1.5 py-0.5 bg-sky-50 text-sky-600 border border-sky-200 rounded font-mono font-medium hidden md:inline shrink-0">Sidecar</span>
      </div>
    </div>

    <!-- Center: Global Switch & Telemetry Pill -->
    <div class="flex items-center gap-2 sm:gap-3 shrink-0">
      <!-- Global Emergency Switch -->
      <button
        v-if="currentUser?.role === 'superuser' && bootstrap"
        @click="toggleGlobalEnabled(!bootstrap.global_enabled)"
        class="flex items-center gap-1 sm:gap-1.5 px-2 sm:px-3 py-1 rounded-full text-[11px] sm:text-xs font-medium border transition cursor-pointer select-none active:scale-98 whitespace-nowrap shrink-0"
        :class="bootstrap.global_enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100' : 'bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100'"
      >
        <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="bootstrap.global_enabled ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"></span>
        <span class="hidden sm:inline">{{ bootstrap.global_enabled ? '全局运行中' : '全局已熔断' }}</span>
        <span class="sm:hidden">{{ bootstrap.global_enabled ? '运行中' : '熔断' }}</span>
      </button>

      <!-- Token & Decisions Metrics Pill -->
      <div v-if="bootstrap" class="hidden md:flex items-center gap-3 px-3 py-1 bg-slate-50 border border-slate-200 rounded-full text-xs font-mono text-slate-600">
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
    <div class="flex items-center gap-1.5 sm:gap-2.5 shrink-0">
      <div v-if="currentUser" class="flex items-center gap-1.5 sm:gap-2">
        <span
          class="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 rounded font-mono border whitespace-nowrap"
          :class="currentUser.role === 'superuser' ? 'bg-indigo-50 text-indigo-700 border-indigo-200 font-medium' : 'bg-slate-100 text-slate-700 border-slate-200 font-medium'"
        >
          <span class="sm:hidden">{{ currentUser.role === 'superuser' ? '超管' : '群管' }}</span>
          <span class="hidden sm:inline">{{ currentUser.role === 'superuser' ? '超级管理员' : '群管理员' }}</span>
        </span>
        <span class="text-xs font-mono text-slate-500 hidden lg:inline">QQ: {{ currentUser.user_id }}</span>
      </div>
      <button @click="logout" class="btn-secondary text-[11px] sm:text-xs py-1 px-2 sm:px-2.5 hover:text-rose-600 hover:border-rose-200 shrink-0 whitespace-nowrap">
        退出
      </button>
    </div>
  </header>
</template>

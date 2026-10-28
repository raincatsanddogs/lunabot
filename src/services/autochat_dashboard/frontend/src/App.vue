<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue';
import { useAuth } from './composables/useAuth';
import { useDashboardState } from './composables/useDashboardState';
import TopBar from './components/TopBar.vue';
import GroupSidebar from './components/GroupSidebar.vue';
import ConfigEditor from './components/ConfigEditor.vue';
import MemoryInspector from './components/MemoryInspector.vue';
import LiveAuditFeed from './components/LiveAuditFeed.vue';
import FeedbackOverlay from './components/FeedbackOverlay.vue';

const { currentUser, authLoading, authError, extractTicketAndLogin } = useAuth();
const { bootstrap, currentGroupId, activeTab, sidebarOpen, loadBootstrap } = useDashboardState();

const handleResize = () => {
  if (typeof window !== 'undefined' && window.innerWidth >= 768) {
    if (activeTab.value === 'audit') {
      activeTab.value = 'config';
    }
    if (sidebarOpen.value) {
      sidebarOpen.value = false;
    }
  }
};

onMounted(async () => {
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', handleResize);
  }
  await extractTicketAndLogin();
  if (currentUser.value) {
    await loadBootstrap();
  }
});

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', handleResize);
  }
});
</script>

<template>
  <!-- Loading Splash -->
  <div v-if="authLoading" class="h-screen w-screen flex flex-col items-center justify-center bg-slate-50 text-slate-600 gap-3 select-none">
    <div class="text-3xl animate-bounce">🌙</div>
    <div class="text-xs font-mono text-slate-500">正在校验会话状态与票据...</div>
  </div>

  <!-- Unauthenticated Screen -->
  <div v-else-if="!currentUser" class="h-screen w-screen flex flex-col items-center justify-center bg-slate-50 p-4 select-none">
    <div class="card-panel max-w-md w-full text-center space-y-4 p-8 shadow-md">
      <div class="text-4xl">🌙</div>
      <h1 class="text-lg font-bold text-slate-800">LunaBot Autochat Dashboard</h1>
      <p class="text-xs text-slate-600 leading-relaxed">
        本系统采用<strong>零密码阅后即焚票据（OTT）</strong>安全鉴权。<br />
        请在 QQ 私聊向 Bot 发送以下指令获取专属免密登录链接：
      </p>
      <div class="p-2.5 bg-slate-100 border border-slate-200 rounded-lg text-sky-700 font-mono text-sm font-semibold select-all">
        /autochat panel
      </div>
      <div v-if="authError" class="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 text-xs rounded-lg">
        {{ authError }}
      </div>
      <div class="text-[11px] text-slate-500">
        票据有效期为 180 秒，单次点击消费后立即销毁。
      </div>
    </div>
  </div>

  <!-- Main Authenticated Dashboard -->
  <div v-else class="h-screen w-screen flex flex-col overflow-hidden bg-slate-50 text-slate-800">
    <TopBar />

    <div class="flex-1 flex overflow-hidden relative">
      <!-- Left sidebar with groups (desktop collapsible + mobile drawer) -->
      <GroupSidebar />

      <!-- Center content workspace -->
      <main class="flex-1 flex flex-col overflow-hidden bg-slate-50 min-w-0">
        <!-- Tab Navigation Bar -->
        <div class="h-10 px-3 md:px-6 border-b border-slate-200 flex items-center justify-between shrink-0 bg-white/80 backdrop-blur">
          <div class="flex gap-2 sm:gap-4 text-xs font-medium">
            <button
              @click="activeTab = 'config'"
              class="h-10 border-b-2 transition flex items-center gap-1 sm:gap-1.5 px-1 sm:px-2 cursor-pointer select-none"
              :class="activeTab === 'config' ? 'border-sky-600 text-sky-600 font-bold' : 'border-transparent text-slate-500 hover:text-slate-800'"
            >
              <span>⚙️ 原生策略</span>
              <span class="hidden sm:inline">与人设</span>
            </button>
            <button
              @click="activeTab = 'memories'"
              class="h-10 border-b-2 transition flex items-center gap-1 sm:gap-1.5 px-1 sm:px-2 cursor-pointer select-none"
              :class="activeTab === 'memories' ? 'border-sky-600 text-sky-600 font-bold' : 'border-transparent text-slate-500 hover:text-slate-800'"
            >
              <span>🧠 长期记忆</span>
              <span class="hidden sm:inline">与素材库</span>
            </button>
            <!-- 3rd Tab on Mobile: 📡 实时决策流 -->
            <button
              @click="activeTab = 'audit'"
              class="h-10 border-b-2 transition flex items-center gap-1 sm:gap-1.5 px-1 sm:px-2 md:hidden cursor-pointer select-none"
              :class="activeTab === 'audit' ? 'border-sky-600 text-sky-600 font-bold' : 'border-transparent text-slate-500 hover:text-slate-800'"
            >
              <span>📡 决策流</span>
            </button>
          </div>

          <div v-if="currentGroupId" class="text-xs text-slate-500 font-mono hidden sm:block truncate ml-2">
            当前群: <span class="text-slate-800 font-semibold">{{ currentGroupId }}</span>
          </div>
        </div>

        <!-- View Content -->
        <div class="flex-1 overflow-hidden flex flex-col min-h-0">
          <ConfigEditor v-if="activeTab === 'config'" />
          <MemoryInspector v-else-if="activeTab === 'memories'" />
          <LiveAuditFeed v-else-if="activeTab === 'audit'" :group-id="currentGroupId || 'all'" :is-full-page="true" />
        </div>

        <!-- Bottom Real-time Live Audit Feed (docked when activeTab is not audit) -->
        <LiveAuditFeed
          v-if="activeTab !== 'audit'"
          :group-id="currentGroupId || 'all'"
        />
      </main>

      <!-- Mobile Drawer Backdrop Mask -->
      <Transition
        enter-active-class="transition-opacity duration-300 ease-out"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="transition-opacity duration-200 ease-in"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-if="sidebarOpen"
          @click="sidebarOpen = false"
          class="fixed inset-0 bg-slate-900/30 backdrop-blur-xs z-30 md:hidden"
        ></div>
      </Transition>
    </div>
  </div>

  <!-- Global Feedback (Toasts and Confirm Dialog) -->
  <FeedbackOverlay />
</template>

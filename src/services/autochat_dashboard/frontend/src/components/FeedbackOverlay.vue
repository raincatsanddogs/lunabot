<script setup lang="ts">
import { useFeedback } from '../composables/useFeedback';

const { toasts, currentConfirm, removeToast, handleConfirm, handleCancel } = useFeedback();
</script>

<template>
  <!-- Toast Notification Container (Top-Right) -->
  <div class="fixed top-4 right-4 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none select-none">
    <TransitionGroup name="toast">
      <div
        v-for="item in toasts"
        :key="item.id"
        class="pointer-events-auto bg-white border border-slate-200/90 shadow-lg rounded-xl p-3.5 flex items-start gap-3 transition-all duration-300"
      >
        <!-- Icon badge -->
        <div class="shrink-0 mt-0.5">
          <div
            v-if="item.type === 'success'"
            class="w-5 h-5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center justify-center"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </div>
          <div
            v-else-if="item.type === 'error'"
            class="w-5 h-5 rounded-full bg-rose-50 text-rose-600 border border-rose-200 flex items-center justify-center"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </div>
          <div
            v-else-if="item.type === 'warning'"
            class="w-5 h-5 rounded-full bg-amber-50 text-amber-600 border border-amber-200 flex items-center justify-center"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
          </div>
          <div
            v-else
            class="w-5 h-5 rounded-full bg-sky-50 text-sky-600 border border-sky-200 flex items-center justify-center"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          </div>
        </div>

        <!-- Message -->
        <div class="flex-1 text-xs text-slate-800 font-medium leading-relaxed pt-0.5">
          {{ item.message }}
        </div>

        <!-- Close button -->
        <button
          @click="removeToast(item.id)"
          class="shrink-0 text-slate-400 hover:text-slate-600 p-0.5 rounded transition"
          title="关闭"
        >
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>

  <!-- Global Confirm Modal Dialog (Center) -->
  <Transition name="modal">
    <div
      v-if="currentConfirm"
      class="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4 select-none"
      @click.self="handleCancel"
    >
      <div class="bg-white rounded-2xl shadow-2xl border border-slate-200/80 max-w-md w-full p-6 space-y-4 animate-modal">
        <!-- Header -->
        <div class="flex items-start gap-3">
          <div
            class="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
            :class="currentConfirm.options.danger ? 'bg-rose-50 text-rose-600 border border-rose-200' : 'bg-sky-50 text-sky-600 border border-sky-200'"
          >
            <svg v-if="currentConfirm.options.danger" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
            <svg v-else class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          </div>
          <div class="flex-1 min-w-0">
            <h3 class="text-base font-bold text-slate-800 leading-snug">
              {{ currentConfirm.options.title }}
            </h3>
            <p class="text-xs text-slate-600 mt-2 leading-relaxed whitespace-pre-wrap">
              {{ currentConfirm.options.message }}
            </p>
          </div>
        </div>

        <!-- Buttons -->
        <div class="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
          <button
            @click="handleCancel"
            class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium text-xs rounded-lg transition active:scale-98"
          >
            {{ currentConfirm.options.cancelText }}
          </button>
          <button
            @click="handleConfirm"
            class="px-4 py-2 font-medium text-xs rounded-lg text-white shadow-sm transition active:scale-98"
            :class="currentConfirm.options.danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-sky-600 hover:bg-sky-700'"
          >
            {{ currentConfirm.options.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(30px) scale(0.95);
}
.toast-leave-to {
  opacity: 0;
  transform: translateY(-10px) scale(0.95);
}

.modal-enter-active,
.modal-leave-active {
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
</style>

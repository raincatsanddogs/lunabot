import { ref } from 'vue';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

export interface ConfirmModalOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

interface ActiveConfirm {
  id: string;
  options: Required<ConfirmModalOptions>;
  resolve: (value: boolean) => void;
}

const toasts = ref<ToastItem[]>([]);
const currentConfirm = ref<ActiveConfirm | null>(null);

let toastIdSeq = 0;
let confirmIdSeq = 0;

const removeToast = (id: string) => {
  const index = toasts.value.findIndex((t) => t.id === id);
  if (index !== -1) {
    toasts.value.splice(index, 1);
  }
};

const showToast = (message: string, type: ToastType = 'info', duration = 3000) => {
  const id = `toast-${Date.now()}-${++toastIdSeq}`;
  toasts.value.push({ id, type, message, duration });
  if (duration > 0) {
    setTimeout(() => {
      removeToast(id);
    }, duration);
  }
  return id;
};

export const toast = {
  success: (msg: string, duration = 3000) => showToast(msg, 'success', duration),
  error: (msg: string, duration = 3000) => showToast(msg, 'error', duration),
  info: (msg: string, duration = 3000) => showToast(msg, 'info', duration),
  warning: (msg: string, duration = 3000) => showToast(msg, 'warning', duration),
};

export const confirmModal = (options: ConfirmModalOptions): Promise<boolean> => {
  return new Promise<boolean>((resolve) => {
    // If a confirm is already visible, resolve existing one as false
    if (currentConfirm.value) {
      currentConfirm.value.resolve(false);
    }
    const id = `confirm-${Date.now()}-${++confirmIdSeq}`;
    currentConfirm.value = {
      id,
      options: {
        title: options.title,
        message: options.message,
        confirmText: options.confirmText ?? '确定',
        cancelText: options.cancelText ?? '取消',
        danger: options.danger ?? false,
      },
      resolve: (result: boolean) => {
        resolve(result);
        if (currentConfirm.value?.id === id) {
          currentConfirm.value = null;
        }
      },
    };
  });
};

export function useFeedback() {
  const handleConfirm = () => {
    if (currentConfirm.value) {
      currentConfirm.value.resolve(true);
    }
  };

  const handleCancel = () => {
    if (currentConfirm.value) {
      currentConfirm.value.resolve(false);
    }
  };

  return {
    toasts,
    currentConfirm,
    removeToast,
    handleConfirm,
    handleCancel,
    toast,
    confirmModal,
  };
}

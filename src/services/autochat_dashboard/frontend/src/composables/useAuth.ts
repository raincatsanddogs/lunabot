import { ref, onMounted } from 'vue';
import type { SessionUser } from '../types/api';
import { apiFetch, clearEtagCache } from '../utils/http';

const currentUser = ref<SessionUser | null>(null);
const authLoading = ref(true);
const authError = ref<string | null>(null);

export function useAuth() {
  const extractTicketAndLogin = async () => {
    authLoading.value = true;
    authError.value = null;

    // 1. Check URL Hash fragment for #ticket=<hex>
    const hash = window.location.hash;
    let rawTicket: string | null = null;

    if (hash.startsWith('#ticket=')) {
      rawTicket = hash.slice('#ticket='.length).trim();
      // Instantly wipe raw ticket from address bar and browser history
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }

    if (rawTicket) {
      try {
        const user = await apiFetch<SessionUser>('/api/v1/auth/exchange', {
          method: 'POST',
          body: JSON.stringify({ ticket: rawTicket }),
        });
        currentUser.value = user;
        authLoading.value = false;
        return;
      } catch (err: any) {
        authError.value = err.message || '登录票据已失效或已过期';
      }
    }

    // 2. Otherwise verify existing cookie session
    try {
      const user = await apiFetch<SessionUser>('/api/v1/auth/me');
      currentUser.value = user;
    } catch {
      currentUser.value = null;
    } finally {
      authLoading.value = false;
    }
  };

  const logout = async () => {
    try {
      await apiFetch('/api/v1/auth/logout', { method: 'POST' });
    } catch {}
    currentUser.value = null;
    clearEtagCache();
  };

  onMounted(() => {
    window.addEventListener('auth:expired', () => {
      currentUser.value = null;
    });
  });

  return {
    currentUser,
    authLoading,
    authError,
    extractTicketAndLogin,
    logout,
  };
}

import { ref } from 'vue';
import type { BootstrapData, GroupConfigDetail, GroupConfigUpdate } from '../types/api';
import { apiFetch } from '../utils/http';
import { toast } from './useFeedback';

const bootstrap = ref<BootstrapData | null>(null);
const currentGroupId = ref<string | null>(null);
const currentConfig = ref<GroupConfigDetail | null>(null);
const loadingConfig = ref(false);
const savingConfig = ref(false);
const activeTab = ref<'config' | 'memories' | 'stickers' | 'sandbox'>('config');

export function useDashboardState() {
  const loadBootstrap = async () => {
    try {
      const data = await apiFetch<BootstrapData>('/api/v1/bootstrap');
      bootstrap.value = data;
      // Select first group if none selected
      if (!currentGroupId.value && data.groups.length > 0) {
        selectGroup(data.groups[0].group_id);
      }
    } catch (err) {
      console.error('Failed to load bootstrap data:', err);
    }
  };

  const selectGroup = async (gid: string) => {
    currentGroupId.value = gid;
    loadingConfig.value = true;
    try {
      currentConfig.value = await apiFetch<GroupConfigDetail>(`/api/v1/groups/${gid}/config`);
    } catch (err) {
      console.error(`Failed to load config for group ${gid}:`, err);
    } finally {
      loadingConfig.value = false;
    }
  };

  const saveConfig = async (updates: GroupConfigUpdate) => {
    if (!currentGroupId.value) return;
    savingConfig.value = true;
    try {
      const updated = await apiFetch<GroupConfigDetail>(
        `/api/v1/groups/${currentGroupId.value}/config`,
        {
          method: 'PUT',
          body: JSON.stringify(updates),
        }
      );
      currentConfig.value = updated;
      // Update in summary list too
      if (bootstrap.value) {
        const item = bootstrap.value.groups.find((g) => g.group_id === currentGroupId.value);
        if (item) {
          if (updated.enabled !== undefined) item.enabled = updated.enabled;
          if (updated.ambient_p !== undefined) item.ambient_p = updated.ambient_p;
          item.updated_at = updated.updated_at;
        }
      }
    } finally {
      savingConfig.value = false;
    }
  };

  const toggleGlobalEnabled = async (enabled: boolean) => {
    try {
      const res = await apiFetch<{ global_enabled: boolean; config_watermark: number }>(
        '/api/v1/global/state',
        {
          method: 'PUT',
          body: JSON.stringify({ global_enabled: enabled }),
        }
      );
      if (bootstrap.value) {
        bootstrap.value.global_enabled = res.global_enabled;
        bootstrap.value.config_watermark = res.config_watermark;
      }
      toast.success(res.global_enabled ? '已开启全局自动接话' : '已熔断全局自动接话');
    } catch (err: any) {
      toast.error(err.message || '更新全局状态失败');
    }
  };

  return {
    bootstrap,
    currentGroupId,
    currentConfig,
    loadingConfig,
    savingConfig,
    activeTab,
    loadBootstrap,
    selectGroup,
    saveConfig,
    toggleGlobalEnabled,
  };
}

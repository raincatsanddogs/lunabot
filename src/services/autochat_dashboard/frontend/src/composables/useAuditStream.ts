import { ref, onMounted, onUnmounted, watch } from 'vue';
import type { AuditLogItem } from '../types/api';
import { HibernatingSSE } from '../utils/sse_manager';

const MAX_LOGS = 200;
const auditLogs = ref<AuditLogItem[]>([]);
const sseOnline = ref(false);
let sseManager: HibernatingSSE | null = null;

export function useAuditStream(targetGroupId = ref('all')) {
  const onBatch = (batch: AuditLogItem[]) => {
    // Append and keep ring buffer <= MAX_LOGS
    const combined = [...batch, ...auditLogs.value];
    if (combined.length > MAX_LOGS) {
      auditLogs.value = combined.slice(0, MAX_LOGS);
    } else {
      auditLogs.value = combined;
    }
  };

  const onStatus = (online: boolean) => {
    sseOnline.value = online;
  };

  const clearLogs = () => {
    auditLogs.value = [];
  };

  onMounted(() => {
    if (!sseManager) {
      sseManager = new HibernatingSSE(onBatch, onStatus);
      sseManager.start(targetGroupId.value);
    }
  });

  watch(targetGroupId, (newGid) => {
    if (sseManager) {
      sseManager.updateGroup(newGid);
    }
  });

  onUnmounted(() => {
    // Keep alive during tab switching, managed globally
  });

  return {
    auditLogs,
    sseOnline,
    clearLogs,
  };
}

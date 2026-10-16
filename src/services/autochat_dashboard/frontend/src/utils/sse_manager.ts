/**
 * Hibernating SSE Client.
 * Automatically sleeps when the browser tab is hidden to save bandwidth/connections,
 * and seamlessly resumes with lastEventId cursor upon tab reactivation.
 */
import type { AuditLogItem } from '../types/api';

export class HibernatingSSE {
  private es: EventSource | null = null;
  private lastId = 0;
  private hideTimer: number | null = null;
  private groupId = 'all';
  private onBatchCallback: (items: AuditLogItem[]) => void;
  private onStatusChange: (online: boolean) => void;

  constructor(
    onBatch: (items: AuditLogItem[]) => void,
    onStatus: (online: boolean) => void
  ) {
    this.onBatchCallback = onBatch;
    this.onStatusChange = onStatus;
    document.addEventListener('visibilitychange', this.handleVisibility);
  }

  public start(groupId = 'all') {
    this.groupId = groupId;
    if (document.visibilityState === 'visible') {
      this.connect();
    }
  }

  public updateGroup(groupId: string) {
    if (this.groupId !== groupId) {
      this.groupId = groupId;
      this.disconnect();
      this.lastId = 0; // Reset cursor for different group
      if (document.visibilityState === 'visible') {
        this.connect();
      }
    }
  }

  private connect() {
    if (this.es) return;
    const url = `/api/v1/telemetry/stream?group_id=${encodeURIComponent(this.groupId)}&since_id=${this.lastId}`;
    this.es = new EventSource(url, { withCredentials: true });

    this.es.onopen = () => this.onStatusChange(true);
    this.es.onerror = () => {
      this.onStatusChange(false);
    };

    this.es.addEventListener('audit_batch', (e: MessageEvent) => {
      if (e.lastEventId) {
        this.lastId = parseInt(e.lastEventId, 10);
      }
      try {
        const batch = JSON.parse(e.data);
        if (Array.isArray(batch) && batch.length > 0) {
          const maxId = batch[batch.length - 1].id;
          if (maxId > this.lastId) this.lastId = maxId;
          this.onBatchCallback(batch);
        }
      } catch (err) {
        console.warn('Failed to parse SSE batch:', err);
      }
    });
  }

  public disconnect() {
    if (this.es) {
      this.es.close();
      this.es = null;
      this.onStatusChange(false);
    }
  }

  private handleVisibility = () => {
    if (document.visibilityState === 'visible') {
      if (this.hideTimer) {
        window.clearTimeout(this.hideTimer);
        this.hideTimer = null;
      }
      // Re-connect and pull incremental logs since lastId
      this.connect();
    } else {
      // Disconnect after 5s in background to bring network usage to strictly 0
      this.hideTimer = window.setTimeout(() => {
        this.disconnect();
      }, 5000);
    }
  };

  public destroy() {
    document.removeEventListener('visibilitychange', this.handleVisibility);
    this.disconnect();
  }
}

/**
 * Numbers and Timestamps Formatting Helpers.
 */

export function formatTokenCount(num: number): string {
  if (num >= 1_000_000) {
    return (num / 1_000_000).toFixed(1) + 'M';
  }
  if (num >= 1_000) {
    return (num / 1_000).toFixed(1) + 'k';
  }
  return num.toString();
}

export function formatRelativeTime(tsSeconds: number): string {
  if (!tsSeconds) return '';
  const diff = Math.floor(Date.now() / 1000 - tsSeconds);
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${diff}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  
  const d = new Date(tsSeconds * 1000);
  const h = String(d.getHours()).padStart(2, '0');
  const m = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

export function formatPercent(val: number): string {
  return `${(val * 100).toFixed(0)}%`;
}

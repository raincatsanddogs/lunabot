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

export function formatPercent(val: number): string {
  return `${(val * 100).toFixed(0)}%`;
}

/**
 * 完整时间格式化：YYYY-MM-DD HH:mm:ss（补齐两位 0），用于 hover 悬浮 :title 提示。
 */
export function formatFullDateTime(tsSeconds: number): string {
  if (!tsSeconds || isNaN(tsSeconds)) return '';
  const d = new Date(tsSeconds * 1000);
  if (isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return `${y}-${m}-${day} ${h}:${min}:${s}`;
}

/**
 * 智能分级时间格式化：
 * - 今天（同年月日）：
 *   - 差值 < 60秒：刚刚 (HH:mm:ss)
 *   - 差值 < 60分钟：X分钟前 (HH:mm)
 *   - 差值 >= 60分钟：今天 HH:mm:ss
 * - 昨天（自然日差 1 天）：昨天 HH:mm:ss
 * - 今年更早日期：MM-DD HH:mm:ss
 * - 跨年更早日期：YYYY-MM-DD HH:mm:ss
 */
export function formatSmartDateTime(tsSeconds: number): string {
  if (!tsSeconds || isNaN(tsSeconds)) return '';
  const now = new Date();
  const target = new Date(tsSeconds * 1000);
  if (isNaN(target.getTime())) return '';

  const y = target.getFullYear();
  const m = String(target.getMonth() + 1).padStart(2, '0');
  const day = String(target.getDate()).padStart(2, '0');
  const h = String(target.getHours()).padStart(2, '0');
  const min = String(target.getMinutes()).padStart(2, '0');
  const s = String(target.getSeconds()).padStart(2, '0');

  const isToday =
    now.getFullYear() === y &&
    now.getMonth() === target.getMonth() &&
    now.getDate() === target.getDate();

  if (isToday) {
    const diff = Math.floor(now.getTime() / 1000 - tsSeconds);
    if (diff < 60) {
      return `刚刚 (${h}:${min}:${s})`;
    }
    if (diff < 3600) {
      const minutes = Math.floor(diff / 60);
      return `${minutes}分钟前 (${h}:${min})`;
    }
    return `今天 ${h}:${min}:${s}`;
  }

  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const isYesterday =
    y === yesterday.getFullYear() &&
    target.getMonth() === yesterday.getMonth() &&
    target.getDate() === yesterday.getDate();

  if (isYesterday) {
    return `昨天 ${h}:${min}:${s}`;
  }

  if (y === now.getFullYear()) {
    return `${m}-${day} ${h}:${min}:${s}`;
  }

  return `${y}-${m}-${day} ${h}:${min}:${s}`;
}

/**
 * 保持 formatRelativeTime 存在并兼容调用（作为 formatSmartDateTime 的包装/别名）
 */
export function formatRelativeTime(tsSeconds: number): string {
  return formatSmartDateTime(tsSeconds);
}

/**
 * QQ Official Avatar CDN Formatter & Multimodal Text Badge Parser.
 * Strictly avoids backend image proxying.
 */

export function getQQUserAvatar(userId: string): string {
  if (!userId || userId === '0') return '';
  return `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(userId)}&s=100`;
}

export function getQQGroupAvatar(groupId: string): string {
  if (!groupId) return '';
  return `https://p.qlogo.cn/gh/${encodeURIComponent(groupId)}/${encodeURIComponent(groupId)}/100/`;
}

export interface TextToken {
  type: 'text' | 'image_badge';
  content: string;
}

export function parseDigestBadges(text: string): TextToken[] {
  if (!text) return [];
  const regex = /\[\[IMG:(.*?)\]\]/g;
  const tokens: TextToken[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }
    tokens.push({ type: 'image_badge', content: match[1] || '图片' });
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: 'text', content: text.slice(lastIndex) });
  }

  return tokens;
}

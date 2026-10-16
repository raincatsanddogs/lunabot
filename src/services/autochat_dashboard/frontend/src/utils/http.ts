/**
 * HTTP Client with In-memory Weak ETag Cache Pool & CSRF Header Injection.
 * Intercepts HTTP 304 and returns cached payloads with 0 network payload.
 */

interface CacheEntry<T = unknown> {
  etag: string;
  data: T;
}

const etagCache = new Map<string, CacheEntry>();

export async function apiFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});

  // 1. Mandatory CSRF protection header
  headers.set('X-Requested-With', 'LunaDashClient');

  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  // 2. Attach If-None-Match for GET requests from local ETag cache
  const cached = method === 'GET' ? etagCache.get(url) : undefined;
  if (cached) {
    headers.set('If-None-Match', cached.etag);
  }

  const res = await fetch(url, {
    ...options,
    method,
    headers,
    credentials: 'same-origin',
  });

  // 3. Handle 304 Not Modified
  if (res.status === 304 && cached) {
    return structuredClone(cached.data) as T;
  }

  // 4. Handle 401 Unauthorized
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth:expired'));
    throw new Error('UNAUTHORIZED');
  }

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errBody.detail || `HTTP ${res.status}`);
  }

  const data = (await res.json()) as T;
  const newEtag = res.headers.get('ETag');

  // 5. Update or invalidate ETag cache
  if (method === 'GET' && newEtag) {
    etagCache.set(url, { etag: newEtag, data: structuredClone(data) });
  } else if (method !== 'GET') {
    // Invalidate bootstrap when mutating
    etagCache.delete('/api/v1/bootstrap');
  }

  return data;
}

export function clearEtagCache() {
  etagCache.clear();
}

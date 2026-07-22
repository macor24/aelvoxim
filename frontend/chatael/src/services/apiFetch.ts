/**
 * apiFetch — A fetch wrapper that handles session conflict detection.
 *
 * If the server returns X-Session-Conflict header, the user's session
 * has been taken over by another device. Forces logout.
 */

import { useAuthStore } from '../stores/authStore';

const SESSION_CONFLICT_EVENT = 'aelvoxim:session-conflict';

/** Listen for session conflict events */
export function onSessionConflict(callback: () => void): () => void {
  const handler = () => callback();
  window.addEventListener(SESSION_CONFLICT_EVENT, handler);
  return () => window.removeEventListener(SESSION_CONFLICT_EVENT, handler);
}

/** Dispatch session conflict event */
function fireSessionConflict() {
  window.dispatchEvent(new CustomEvent(SESSION_CONFLICT_EVENT));
}

/**
 * Wrapped fetch that checks for X-Session-Conflict header.
 * Use this instead of raw fetch for all API calls.
 */
export async function apiFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const res = await fetch(url, options);

  if (res.headers.get('X-Session-Conflict') === 'true') {
    fireSessionConflict();
    try {
      const store = useAuthStore.getState();
      store.tenants.forEach((t: { id: string }) => store.removeTenant(t.id));
    } catch {}
    throw new Error('session_conflict');
  }

  return res;
}

/**
 * Convenience: GET with auth header already set.
 */
export async function apiGet(url: string): Promise<Response> {
  const tenant = useAuthStore.getState().getActiveTenant();
  const key = tenant?.apiKey || '';
  return apiFetch(url, {
    headers: { Authorization: 'Bearer ' + key },
  });
}

/**
 * Convenience: POST with JSON body + auth header.
 */
export async function apiPost(url: string, body: unknown): Promise<Response> {
  const tenant = useAuthStore.getState().getActiveTenant();
  const key = tenant?.apiKey || '';
  return apiFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + key,
    },
    body: JSON.stringify(body),
  });
}

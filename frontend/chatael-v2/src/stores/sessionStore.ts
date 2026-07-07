import { create } from 'zustand';
import type { Session } from '../types/chat';
import { getIsolationSuffix } from './authStore';

function genId() { return Math.random().toString(36).substring(2, 10); }
function now() { return new Date().toISOString(); }

/** API key for PG sync (from authStore) */
function apiKey(): string {
  try {
    const raw = localStorage.getItem('chatael_tenants');
    if (!raw) return '';
    const tenants = JSON.parse(raw);
    const active = tenants?.[0];
    return active?.apiKey || '';
  } catch { return ''; }
}

/** Sync a session to PG (fire-and-forget) — returns PG session id on success */
function syncToPG(session: Session): Promise<string | null> {
  const key = apiKey();
  if (!key || !session.id) return Promise.resolve(null);
  const baseUrl = (() => {
    try {
      const raw = localStorage.getItem('chatael_tenants');
      if (!raw) return 'http://127.0.0.1:9701';
      const tenants = JSON.parse(raw);
      const active = tenants?.[0];
      return active?.apiUrl || 'http://127.0.0.1:9701';
    } catch { return 'http://127.0.0.1:9701'; }
  })().replace(/\/+$/, '');
  return fetch(`${baseUrl}/v1/chat/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({ session: { ...session, messages: [] } }),
  })
    .then(r => r.json())
    .then(d => d.pg_id || null)
    .catch(() => null);
}

function storageKey(): string {
  return 'chatael_sessions' + getIsolationSuffix();
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(storageKey());
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveSessions(sessions: Session[]) {
  try { localStorage.setItem(storageKey(), JSON.stringify(sessions)); } catch {}
}

// Ensure at least one session exists on first visit
function ensureSession(sessions: Session[]): Session[] {
  if (sessions.length > 0) return sessions;
  const id = genId();
  const s: Session = { id, title: '新对话', createdAt: now(), updatedAt: now() };
  saveSessions([s]);
  return [s];
}

interface SessionState {
  sessions: Session[];
  activeSessionId: string | null;
  setActiveSession: (id: string) => void;
  createSession: () => string;
  renameSession: (id: string, title: string) => void;
  deleteSession: (id: string) => void;
}

export const useSessionStore = create<SessionState>((set, get) => {
  const sessions = ensureSession(loadSessions());
  // Lazily merge backend sessions on first access
  setTimeout(() => {
    const key = apiKey();
    if (!key) return;
    import('../services/chatHistory').then(({ fetchSessions }) => {
      fetchSessions().then(backendSessions => {
        if (!backendSessions || backendSessions.length === 0) return;
        const existing = get().sessions;
        const existingIds = new Set(existing.map(s => s.id));
        const merged = [...existing];
        let changed = false;
        for (const bs of backendSessions) {
          if (!existingIds.has(bs.id)) {
            merged.push({
              id: bs.id,
              title: bs.title || '新对话',
              createdAt: bs.created_at || new Date().toISOString(),
              updatedAt: bs.updated_at || new Date().toISOString(),
            });
            changed = true;
          }
        }
        if (changed) {
          merged.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
          saveSessions(merged);
          set({ sessions: merged });
        }
      }).catch(() => {});
    });
  }, 1000);
  return {
    sessions,
    activeSessionId: sessions[0]?.id || null,
    setActiveSession: (id) => set({ activeSessionId: id }),
    createSession: () => {
      const id = genId();
      const session: Session = { id, title: '新对话', createdAt: now(), updatedAt: now() };
      set((s) => {
        const sessions = [session, ...s.sessions];
        saveSessions(sessions);
        syncToPG(session);  // sync to PG
        return { sessions, activeSessionId: id };
      });
      return id;
    },
    renameSession: (id, title) => set((s) => {
      const sessions = s.sessions.map((sess) =>
        sess.id === id ? { ...sess, title, updatedAt: now() } : sess
      );
      saveSessions(sessions);
      return { sessions };
    }),
    deleteSession: (id) => set((s) => {
      const sessions = s.sessions.filter((sess) => sess.id !== id);
      saveSessions(sessions);
      // Also delete from backend if applicable
      import('../services/chatHistory').then(({ deleteSession }) => {
        deleteSession(id);
      });
      return {
        sessions,
        activeSessionId: s.activeSessionId === id ? (sessions[0]?.id || null) : s.activeSessionId,
      };
    }),
  };
});

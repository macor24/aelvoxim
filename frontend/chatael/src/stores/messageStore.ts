import { create } from 'zustand';
import type { Message } from '../types/chat';
import { getIsolationSuffix } from './authStore';
import { useAuthStore, getApiBase } from './authStore';

function storageKey(): string {
  return 'chatael_messages' + getIsolationSuffix();
}

function loadStreams(): Record<string, Message[]> {
  try {
    const raw = localStorage.getItem(storageKey());
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

// Throttled localStorage persistence: streaming updates write per token,
// which would serialize the whole store on every chunk. Coalesce writes
// into one flush 400ms after the last change (plus a trailing flush when
// the page is hidden/closed).
let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingStreams: Record<string, Message[]> | null = null;

function saveStreams(streams: Record<string, Message[]>) {
  _pendingStreams = streams;
  if (_saveTimer) return; // already scheduled — will pick up latest state
  _saveTimer = setTimeout(() => {
    _saveTimer = null;
    if (_pendingStreams !== null) {
      try { localStorage.setItem(storageKey(), JSON.stringify(_pendingStreams)); } catch {}
      _pendingStreams = null;
    }
  }, 400);
}

// Trailing flush on page hide/close so the latest streamed messages survive
// a quick tab close (best-effort; setTimeout may be throttled in background).
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    if (_saveTimer !== null) {
      clearTimeout(_saveTimer);
      _saveTimer = null;
      if (_pendingStreams !== null) {
        try { localStorage.setItem(storageKey(), JSON.stringify(_pendingStreams)); } catch {}
        _pendingStreams = null;
      }
    }
  });
}

/** API key + baseUrl for PG sync — debounced per session (500ms) so burst
 *  updates (e.g. addMessage + updateMessage in one turn) collapse to one
 *  POST instead of hammering the rate limit. */
const _syncTimers: Record<string, ReturnType<typeof setTimeout>> = {};

function syncToPG(sessionId: string, messages: Message[]) {
  const tenant = useAuthStore.getState().getActiveTenant();
  if (!tenant.apiKey) return;
  const baseUrl = getApiBase();
  if (_syncTimers[sessionId]) clearTimeout(_syncTimers[sessionId]);
  _syncTimers[sessionId] = setTimeout(() => {
    delete _syncTimers[sessionId];
    const msgs = messages.map(m => ({
      role: m.role, content: m.content, timestamp: m.timestamp,
    }));
    fetch(`${baseUrl}/v1/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tenant.apiKey}` },
      body: JSON.stringify({ messages: msgs }),
    }).catch(() => {});
  }, 500);
}

interface MessageState {
  streams: Record<string, Message[]>;
  streamingSessions: Set<string>;
  getMessages: (sessionId: string) => Message[];
  addMessage: (sessionId: string, msg: Message) => void;
  /** Bulk-load messages from backend WITHOUT triggering a PG write-back.
   *  Loading history is a read — never echo it back to the server. */
  loadMessages: (sessionId: string, msgs: Message[]) => void;
  updateMessage: (sessionId: string, msgId: string, updates: Partial<Message>) => void;
  deleteMessage: (sessionId: string, msgId: string) => void;
  clearSession: (sessionId: string) => void;
  setStreaming: (sessionId: string, v: boolean) => void;
  isStreaming: (sessionId: string) => boolean;
}

export const useMessageStore = create<MessageState>((set, get) => ({
  streams: loadStreams(),
  streamingSessions: new Set(),
  getMessages: (sessionId) => get().streams[sessionId] || [],
  addMessage: (sessionId, msg) => set((s) => {
    const streams = { ...s.streams, [sessionId]: [...(s.streams[sessionId] || []), msg] };
    saveStreams(streams);
    if (msg.status === 'done' || msg.status === 'error') {
      syncToPG(sessionId, streams[sessionId]);
    }
    return { streams };
  }),
  loadMessages: (sessionId, msgs) => set((s) => {
    const streams = { ...s.streams, [sessionId]: msgs };
    saveStreams(streams);
    return { streams };
  }),
  updateMessage: (sessionId, msgId, updates) => set((s) => {
    const streams = {
      ...s.streams,
      [sessionId]: (s.streams[sessionId] || []).map((m) =>
        m.id === msgId ? { ...m, ...updates } : m
      ),
    };
    saveStreams(streams);
    if (updates.status === 'done' || updates.status === 'error') {
      syncToPG(sessionId, streams[sessionId]);
    }
    return { streams };
  }),
  deleteMessage: (sessionId, msgId) => set((s) => {
    const streams = {
      ...s.streams,
      [sessionId]: (s.streams[sessionId] || []).filter((m) => m.id !== msgId),
    };
    saveStreams(streams);
    // Note: backend has no single-message DELETE and /messages is append-only
    // (not replace), so PG cannot be synced here without duplicating rows.
    // Deleted messages remain in PG history (accepted limitation); local
    // deletion still hides them immediately.
    return { streams };
  }),
  clearSession: (sessionId) => set((s) => {
    const streams = { ...s.streams, [sessionId]: [] };
    saveStreams(streams);
    return { streams };
  }),
  setStreaming: (sessionId, v) => set((s) => {
    const next = new Set(s.streamingSessions);
    v ? next.add(sessionId) : next.delete(sessionId);
    return { streamingSessions: next };
  }),
  isStreaming: (sessionId) => get().streamingSessions.has(sessionId),
}));

import { create } from 'zustand';
import type { Message } from '../types/chat';
import { getIsolationSuffix } from './authStore';
import { useAuthStore } from './authStore';

function storageKey(): string {
  return 'chatael_messages' + getIsolationSuffix();
}

function loadStreams(): Record<string, Message[]> {
  try {
    const raw = localStorage.getItem(storageKey());
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveStreams(streams: Record<string, Message[]>) {
  try { localStorage.setItem(storageKey(), JSON.stringify(streams)); } catch {}
}

/** API key + baseUrl for PG sync */
function syncToPG(sessionId: string, messages: Message[]) {
  const tenant = useAuthStore.getState().getActiveTenant();
  if (!tenant.apiKey) return;
  const baseUrl = (tenant.apiUrl || 'http://8.134.185.33:9701').replace(/\/+$/, '');
  const msgs = messages.map(m => ({
    role: m.role, content: m.content, timestamp: m.timestamp,
  }));
  fetch(`${baseUrl}/v1/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tenant.apiKey}` },
    body: JSON.stringify({ messages: msgs }),
  }).catch(() => {});
}

interface MessageState {
  streams: Record<string, Message[]>;
  streamingSessions: Set<string>;
  getMessages: (sessionId: string) => Message[];
  addMessage: (sessionId: string, msg: Message) => void;
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

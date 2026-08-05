import { useMemo, useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Database } from 'lucide-react';
import { useSessionStore } from '../../stores/sessionStore';
import { useAuthStore } from '../../stores/authStore';
import { useMessageStore } from '../../stores/messageStore';
import { fetchSessions, fetchSessionMessages } from '../../services/chatHistory';
import SessionItem from './SessionItem';
import type { BackendSession } from '../../services/chatHistory';

/** Parse a timestamp (ISO with T or PG "YYYY-MM-DD HH:MM:SS") to epoch ms; 0 on failure */
const ts = (v: string | undefined | null) => {
  if (!v) return 0;
  const t = Date.parse(v.replace(' ', 'T'));
  return isNaN(t) ? 0 : t;
};

export default function SessionList() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [backendSessions, setBackendSessions] = useState<BackendSession[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [searchResults, setSearchResults] = useState<BackendSession[] | null>(null);
  const { sessions, activeSessionId, setActiveSession, renameSession, deleteSession } = useSessionStore();
  const loadMessages = useMessageStore((s) => s.loadMessages);
  const clearSession = useMessageStore((s) => s.clearSession);
  const activeTenant = useAuthStore((s) => {
    const tenants = s.tenants;
    if (!tenants || tenants.length === 0) return s.getActiveTenant();
    return tenants.find((t) => t.id === s.activeTenantId) || tenants[0];
  });

  // Load backend sessions when logged in — refetch only when the tenant
  // changes (not on every session mutation, which re-pulled 1000 rows).
  useEffect(() => {
    if (!activeTenant.apiKey) { setBackendSessions([]); return; }
    setLoadingHistory(true);
    fetchSessions(1000).then(setBackendSessions).catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [activeTenant.apiKey]);

  // Debounced backend search (same origin — served from 9702 in prod,
  // proxied in dev). Uses relative path so remote deployments work.
  useEffect(() => {
    if (!search.trim() || !activeTenant.apiKey) { setSearchResults(null); return; }
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/sessions/search?q=${encodeURIComponent(search)}`, {
          headers: { Authorization: `Bearer ${activeTenant.apiKey}` },
        });
        setSearchResults((await res.json()).sessions || []);
      } catch { setSearchResults([]); }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, activeTenant.apiKey]);

  // Merge local + all backend sessions (including fragmented history),
  // sorted newest-first by last-updated time — flat list, no day groups.
  const allItems = useMemo(() => {
    const local = sessions.map((s) => ({
      id: s.id,
      title: s.title,
      updatedAt: s.updatedAt,
      isBackend: backendSessions.some((bs) => bs.id === s.id),
    }));
    const backend = backendSessions
      .filter((bs) => !local.some((l) => l.id === bs.id))
      .map((bs) => ({
        id: bs.id,
        title: bs.title || '对话',
        updatedAt: bs.updated_at || bs.created_at || '',
        isBackend: true,
      }));
    const merged = [...local, ...backend].sort(
      (a, b) => ts(b.updatedAt) - ts(a.updatedAt),
    );
    const seen = new Set<string>();
    return merged.filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }, [sessions, backendSessions]);

  // When searching, show search results instead of merged list
  const displayItems = searchResults !== null
    ? searchResults.map((bs) => ({
        id: bs.id,
        title: bs.title || '对话',
        updatedAt: bs.updated_at || bs.created_at || '',
        isBackend: true as const,
      }))
    : allItems.filter((item) => item.title.toLowerCase().includes(search.toLowerCase()));

  const handleDelete = useCallback((id: string) => {
    deleteSession(id);  // store: local + backend DELETE
    // Keep the rendered list in sync without a full refetch
    setBackendSessions((prev) => prev.filter((bs) => bs.id !== id));
  }, [deleteSession]);

  const handleSelect = useCallback(async (id: string, isBackend: boolean) => {
    setActiveSession(id);
    if (!isBackend) return;
    clearSession(id);
    const msgs = await fetchSessionMessages(id).catch(() => []);
    // loadMessages = read-only bulk load; must NOT echo back to PG
    // (addMessage per-message triggered a syncToPG storm → 429 rate limit)
    loadMessages(id, msgs.map((m) => ({
      id: m.id, role: m.role as 'user' | 'assistant', content: m.content,
      timestamp: m.created_at, status: 'done' as const,
    })));
  }, [setActiveSession, clearSession, loadMessages]);

  const renderItem = useCallback((item: { id: string; title: string; isBackend: boolean }, extraClass = '') => (
    <div key={item.id} className="relative">
      {item.isBackend && (
        <div className="absolute left-1 top-1/2 -translate-y-1/2">
          <Database size={10} className="text-blue-400" />
        </div>
      )}
      <SessionItem
        session={{ id: item.id, title: item.title, createdAt: '', updatedAt: '' }}
        isActive={item.id === activeSessionId}
        onSelect={(id) => handleSelect(id, item.isBackend)}
        onRename={renameSession}
        onDelete={handleDelete}
        extraClass={item.isBackend ? 'pl-7' + extraClass : extraClass}
      />
    </div>
  ), [activeSessionId, handleSelect, handleDelete, renameSession]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800">
          <Search size={14} className="text-gray-400 shrink-0" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('sidebar.search')}
            className="flex-1 bg-transparent text-sm outline-none text-gray-700 dark:bg-gray-800 placeholder-gray-400" />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-thin px-1">
        {/* Flat session list — newest first */}
        {displayItems.map((item) => renderItem(item))}
        {displayItems.length === 0 && (
          <p className="text-center text-xs text-gray-400 mt-8">
            {search ? '未找到匹配的会话' : (loadingHistory ? '加载中...' : t('sidebar.noSessions'))}
          </p>
        )}
      </div>
    </div>
  );
}

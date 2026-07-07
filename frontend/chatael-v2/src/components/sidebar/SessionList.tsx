import { useMemo, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Database } from 'lucide-react';
import { useSessionStore } from '../../stores/sessionStore';
import { useAuthStore } from '../../stores/authStore';
import { useMessageStore } from '../../stores/messageStore';
import { fetchSessions, fetchSessionMessages } from '../../services/chatHistory';
import SessionItem from './SessionItem';
import type { BackendSession } from '../../services/chatHistory';

export default function SessionList() {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [backendSessions, setBackendSessions] = useState<BackendSession[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [searchResults, setSearchResults] = useState<BackendSession[] | null>(null);
  const { sessions, activeSessionId, setActiveSession, renameSession, deleteSession } = useSessionStore();
  const addMessage = useMessageStore((s) => s.addMessage);
  const clearSession = useMessageStore((s) => s.clearSession);
  const activeTenant = useAuthStore((s) => {
    const tenants = s.tenants;
    if (!tenants || tenants.length === 0) return s.getActiveTenant();
    return tenants.find((t) => t.id === s.activeTenantId) || tenants[0];
  });

  // Load backend sessions when logged in or when local sessions change
  useEffect(() => {
    if (!activeTenant.apiKey) { setBackendSessions([]); return; }
    const controller = new AbortController();
    setLoadingHistory(true);
    fetchSessions(50).then(setBackendSessions).catch(() => {})
      .finally(() => setLoadingHistory(false));
    return () => controller.abort();
  }, [activeTenant.apiKey, sessions.map(s => s.id + s.updatedAt).join(',')]);

  // Debounced backend search
  useEffect(() => {
    if (!search.trim() || !activeTenant.apiKey) { setSearchResults(null); return; }
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(`http://127.0.0.1:9702/api/sessions/search?q=${encodeURIComponent(search)}`, {
          headers: { Authorization: `Bearer ${activeTenant.apiKey}` },
        });
        setSearchResults((await res.json()).sessions || []);
      } catch { setSearchResults([]); }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, activeTenant.apiKey]);

  // Merge local + backend sessions
  const allItems = useMemo(() => {
    const backendIds = new Set(backendSessions.map((bs) => bs.id));
    const local = sessions.map((s) => ({
      id: s.id,
      title: s.title,
      isBackend: backendIds.has(s.id),  // 如果后端已有此 session，标数据库图标
    }));
    const backend = backendSessions
      .filter((bs) => !backendIds.has(bs.id) || !sessions.some((s) => s.id === bs.id))
      .map((bs) => ({ id: bs.id, title: bs.title, isBackend: true }));
    const merged = [...local, ...backend];
    // Dedup by id
    const seen = new Set();
    return merged.filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }, [sessions, backendSessions]);

  // When searching, show search results instead of merged list
  const displayItems = searchResults !== null
    ? searchResults.map((bs) => ({ id: bs.id, title: bs.title, isBackend: true as const }))
    : allItems.filter((item) => item.title.toLowerCase().includes(search.toLowerCase()));

  const handleSelect = async (id: string, isBackend: boolean) => {
    setActiveSession(id);
    if (!isBackend) return;
    clearSession(id);
    const msgs = await fetchSessionMessages(id).catch(() => []);
    for (const m of msgs) {
      addMessage(id, { id: m.id, role: m.role as 'user' | 'assistant', content: m.content, timestamp: m.created_at, status: 'done' });
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 pb-2">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800">
          <Search size={14} className="text-gray-400 shrink-0" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('sidebar.search')}
            className="flex-1 bg-transparent text-sm outline-none text-gray-700 dark:text-gray-300 placeholder-gray-400" />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-thin px-1">
        {displayItems.map((item) => (
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
              onDelete={deleteSession}
              extraClass={item.isBackend ? 'pl-7' : ''}
            />
          </div>
        ))}
        {displayItems.length === 0 && (
          <p className="text-center text-xs text-gray-400 mt-8">
            {search ? '未找到匹配的会话' : (loadingHistory ? '加载中...' : t('sidebar.noSessions'))}
          </p>
        )}
      </div>
    </div>
  );
}

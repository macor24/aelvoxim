import { useSessionStore } from '../../stores/sessionStore';
import { useAuthStore } from '../../stores/authStore';
import { useChat } from '../../hooks/useChat';
import MessageList from './MessageList';
import MessageInput from './MessageInput';
import LanguageToggle from '../common/LanguageToggle';
import ThemeToggle from '../common/ThemeToggle';
import AuthModal from '../common/AuthModal';
import { useState } from 'react';
import { User } from 'lucide-react';

export default function ChatArea() {
  const activeId = useSessionStore((s) => s.activeSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const tenant = useAuthStore((s) => s.getActiveTenant());
  const { send, isStreaming } = useChat();
  const activeSession = sessions.find((s) => s.id === activeId);
  const [showAuth, setShowAuth] = useState(false);
  const isLoggedIn = tenant && !!tenant.apiKey;

  if (!activeId) {
    return (
      <main className="flex-1 flex items-center justify-center bg-gray-100 dark:bg-gray-900">
        <div className="text-center animate-fade-in">
          <div className="w-20 h-20 mx-auto mb-6 rounded-3xl bg-gradient-brand flex items-center justify-center shadow-soft-lg">
            <span className="text-4xl">🧠</span>
          </div>
          <h2 className="text-2xl font-semibold text-gradient mb-2">Aelvoxim</h2>
          <p className="text-gray-400 text-sm">选择或创建一个会话开始对话</p>
        </div>
      </main>
    );
  }

  return (
    <main className="flex-1 flex flex-col bg-gray-100 dark:bg-gray-900 min-w-0">
      {/* 顶边栏 — 登录、语言、主题 */}
      <div className="flex items-center justify-end gap-1 px-4 py-2 bg-white/80 dark:bg-gray-950/80 backdrop-blur-sm border-b border-gray-200 dark:border-gray-800">
        <button onClick={() => setShowAuth(true)}
          className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition-colors"
          title={isLoggedIn ? tenant!.name : '登录'}>
          <User size={16} />
        </button>
        <LanguageToggle />
        <ThemeToggle />
      </div>
      <MessageList />
      <MessageInput onSend={send} disabled={isStreaming} />
      <AuthModal open={showAuth} onClose={() => setShowAuth(false)} />
    </main>
  );
}

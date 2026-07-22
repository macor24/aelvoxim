import { useMessageStore } from '../../stores/messageStore';
import { useSessionStore } from '../../stores/sessionStore';
import { useAuthStore } from '../../stores/authStore';
import { useAutoScroll } from '../../hooks/useAutoScroll';
import { useChat } from '../../hooks/useChat';
import MessageItem from './MessageItem';
import { useTranslation } from 'react-i18next';

export default function MessageList() {
  const { t } = useTranslation();
  const activeId = useSessionStore((s) => s.activeSessionId);
  const messages = useMessageStore((s) => (activeId ? s.streams[activeId] || [] : []));
  const deleteMessage = useMessageStore((s) => s.deleteMessage);
  const scrollRef = useAutoScroll([messages], activeId);
  const tenant = useAuthStore((s) => s.getActiveTenant());
  const tenantName = tenant?.name || '你';
  const { retryLast } = useChat();

  if (!activeId) return null;

  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin py-4 max-w-4xl mx-auto px-2 md:px-8 w-full">
      {messages.length === 0 ? (
        <div className="flex items-center justify-center h-full text-gray-400 text-sm">{t('chat.noMessages')}</div>
      ) : (
        messages.map((msg) => <MessageItem key={msg.id} message={msg} tenantName={tenantName} onDelete={(id) => deleteMessage(activeId!, id)} onRetry={msg.status === 'error' ? retryLast : undefined} />)
      )}
      <div ref={scrollRef} />
    </div>
  );
}

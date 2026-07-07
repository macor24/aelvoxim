import { useCallback, useState } from 'react';
import { useMessageStore } from '../stores/messageStore';
import { useSessionStore } from '../stores/sessionStore';
import { sendChatMessage } from '../services/chatService';
import type { Message } from '../types/chat';

function genId() { return Math.random().toString(36).substring(2, 10); }
function now() { return new Date().toISOString(); }

function simulateTyping(
  fullText: string,
  onChar: (revealed: string) => void,
  onDone: () => void,
): () => void {
  // Instant: show full text immediately, defer onDone
  onChar(fullText);
  return () => {};
}

export function useChat() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const addMessage = useMessageStore((s) => s.addMessage);
  const updateMessage = useMessageStore((s) => s.updateMessage);
  const setStoreStreaming = useMessageStore((s) => s.setStreaming);
  const storeIsStreaming = useMessageStore((s) => (sessionId: string) => s.streamingSessions.has(sessionId));
  const getMessages = useMessageStore((s) => s.getMessages);
  const renameSession = useSessionStore((s) => s.renameSession);
  const [localStreaming, setLocalStreaming] = useState(false);

  const isStreaming = activeSessionId
    ? (localStreaming || storeIsStreaming(activeSessionId))
    : false;

  const send = useCallback(async (content: string) => {
    if (!activeSessionId || !content.trim() || isStreaming) return;
    const sessionId = activeSessionId;

    const userMsg: Message = { id: genId(), role: 'user', content, timestamp: now(), status: 'done' };
    const aiMsgId = genId();
    const aiMsg: Message = { id: aiMsgId, role: 'assistant', content: '', timestamp: now(), status: 'streaming' };

    addMessage(sessionId, userMsg);
    addMessage(sessionId, aiMsg);
    setStoreStreaming(sessionId, true);
    setLocalStreaming(true);

    renameSession(sessionId, content.slice(0, 30) + (content.length > 30 ? '...' : ''));

    const history = getMessages(sessionId).filter(m => m.id !== aiMsgId);
    // Only send recent 3 turns to avoid drowning the LLM in history
    const recentHistory = history.slice(-6); // up to 3 user + 3 assistant

    let accumulated = '';
    await sendChatMessage(
      recentHistory.map(m => ({ role: m.role, content: m.content })),
      sessionId,
      (token) => {
        accumulated += token;
        updateMessage(sessionId, aiMsgId, { content: accumulated });
      },
      () => {
        updateMessage(sessionId, aiMsgId, { status: 'done' });
        setStoreStreaming(sessionId, false);
        setLocalStreaming(false);
        setTimeout(() => {
          const el = document.querySelector('textarea');
          if (el) el.focus();
        }, 100);
      },
      (err) => {
        updateMessage(sessionId, aiMsgId, { status: 'error', content: err.message });
        setStoreStreaming(sessionId, false);
        setLocalStreaming(false);
      }
    );
  }, [activeSessionId, isStreaming, addMessage, updateMessage, setStoreStreaming, getMessages, renameSession]);

  return { send, isStreaming, retryLast };

  function retryLast() {
    if (!activeSessionId) return;
    const msgs = getMessages(activeSessionId);
    // 找最后一条用户消息
    let lastUser = '';
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') {
        lastUser = msgs[i].content;
        break;
      }
    }
    if (lastUser) send(lastUser);
  }
}

import { useEffect, useRef } from 'react';

export function useAutoScroll(messages: unknown[], sessionId: string | null) {
  const ref = useRef<HTMLDivElement>(null);
  const prevSessionId = useRef<string | null>(null);
  const isInitial = useRef(true);

  useEffect(() => {
    // 初次挂载 — 瞬间定位到底部
    if (isInitial.current) {
      isInitial.current = false;
      prevSessionId.current = sessionId;
      ref.current?.scrollIntoView({ behavior: 'instant' });
      return;
    }

    // 切换会话 — 不滚动，让浏览器自然显示顶部
    if (sessionId !== prevSessionId.current) {
      prevSessionId.current = sessionId;
      return;
    }

    // 同一会话新增消息 — 平滑滚动到底部
    ref.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sessionId]);

  return ref;
}

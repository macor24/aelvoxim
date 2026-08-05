import { useEffect, useRef } from 'react';

export function useAutoScroll(messages: unknown[], sessionId: string | null) {
  const ref = useRef<HTMLDivElement>(null);
  const prevSessionId = useRef<string | null>(null);
  const isInitial = useRef(true);
  const rafRef = useRef<number | null>(null);
  const lastScroll = useRef(0);

  // Throttled smooth scroll: coalesce rapid message updates (streaming)
  // into at most one scroll per frame, and skip if we scrolled very recently.
  const scrollToBottom = (behavior: ScrollBehavior) => {
    const now = Date.now();
    if (behavior === 'smooth' && now - lastScroll.current < 80) {
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = null;
          lastScroll.current = Date.now();
          ref.current?.scrollIntoView({ behavior: 'smooth' });
        });
      }
      return;
    }
    lastScroll.current = now;
    ref.current?.scrollIntoView({ behavior });
  };

  useEffect(() => {
    // 初次挂载 — 瞬间定位到底部
    if (isInitial.current) {
      isInitial.current = false;
      prevSessionId.current = sessionId;
      scrollToBottom('instant');
      return;
    }

    // 切换会话 — 不滚动，让浏览器自然显示顶部
    if (sessionId !== prevSessionId.current) {
      prevSessionId.current = sessionId;
      return;
    }

    // 同一会话新增消息 — 节流平滑滚动到底部
    scrollToBottom('smooth');
  }, [messages, sessionId]);

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return ref;
}

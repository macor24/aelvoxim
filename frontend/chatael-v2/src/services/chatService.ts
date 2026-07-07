import { useAuthStore } from '../stores/authStore';

/** Track consecutive timeouts per session for adaptive replies */
const _timeoutCounts: Record<string, number> = {};

/** Generate adaptive reply based on consecutive timeout count */
function metacogTimeoutReply(sessionId: string): string {
  const count = (_timeoutCounts[sessionId] || 0) + 1;
  _timeoutCounts[sessionId] = count;

  if (count >= 5) {
    return `服务连接失败 ${count} 次，自动重试已停止。请检查服务是否运行，或稍后再试。`;
  }
  if (count >= 3) {
    return `服务连接超时（第 ${count} 次），请检查服务状态。正在重试…`;
  }
  return '请求超时，正在重试…';
}

/** Reset timeout counter on successful reply */
export function resetTimeoutCount(sessionId: string) {
  delete _timeoutCounts[sessionId];
}

/** Call DeepSeek API directly as fallback */
async function callDeepSeek(
  history: { role: string; content: string }[],
  apiKey: string,
  model: string,
): Promise<string | null> {
  const messages = history.map(m => ({ role: m.role, content: m.content }));
  const res = await fetch('https://api.deepseek.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
    body: JSON.stringify({ model, messages, temperature: 0.7, max_tokens: 4096 }),
    signal: AbortSignal.timeout(45000),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data?.choices?.[0]?.message?.content || null;
}

/** Classify fetch errors into user-friendly messages */
function classifyError(err: any): string {
  // HTTP errors with status
  if (err?.status || (err?.message && err.message.startsWith('HTTP '))) {
    const status = err.status || parseInt(err.message?.split(' ')[1] || '0');
    if (status === 401 || status === 403) {
      return 'API Key 无效，请重新登录';
    }
    if (status === 429) {
      return '请求过于频繁，请稍后重试';
    }
    if (status >= 500) {
      return '服务端错误，请稍后重试';
    }
    return `请求失败 (HTTP ${status})`;
  }

  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();

  // Timeout
  if (msg.includes('timeout') || msg.includes('timed out') || (err instanceof DOMException && err.name === 'AbortError')) {
    return '请求超时，请稍后重试';
  }

  // Network error (Failed to fetch, NetworkError, etc.)
  if (msg.includes('fetch') || msg.includes('network') || msg.includes('econnrefused') ||
      msg.includes('networkerror') || msg.includes('failed to fetch')) {
    return '后端不可达，请检查服务是否运行';
  }

  // Fallback — don't expose raw error details
  return '发送失败，请检查网络连接';
}

export async function sendChatMessage(
  history: { role: string; content: string }[],
  sessionId: string,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (err: Error) => void
): Promise<void> {
  try {
    const tenant = useAuthStore.getState().getActiveTenant();
    const apiBase = tenant.apiUrl.replace(/\/+$/, '');
    const lastMsg = history[history.length - 1];
    const query = lastMsg?.content || '';

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 35000);

    const body = JSON.stringify({
      messages: history,
      mode: 'simple',
    });

    // Use streaming endpoint
    const res = await fetch(`${apiBase}/v1/llm/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${tenant.apiKey}`,
      },
      body,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!res.ok) {
      const errBody = await res.text().catch(() => '');
      const err = new Error(`HTTP ${res.status}`);
      // status attached for classifyError
      (err as Error & {status: number}).status = res.status;
      throw err;
    }

    // SSE stream reading
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      if (!text) continue;

      buffer += text;
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data.trim() === '[DONE]') {
            onDone();
            resetTimeoutCount(sessionId);
            return;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed.token) {
              onToken(parsed.token);
            } else if (parsed.error) {
              onError(new Error(parsed.error));
              return;
            }
          } catch { /* skip malformed SSE lines */ }
        }
      }
    }

    // Stream ended without [DONE] — normal completion
    onDone();
    resetTimeoutCount(sessionId);
  } catch (err: any) {

    if (err instanceof DOMException && err.name === 'AbortError') {
      // Try DeepSeek fallback if configured
      const tenant = useAuthStore.getState().getActiveTenant();
      if (tenant.deepseekKey) {
        try {
          const ds = await callDeepSeek(history, tenant.deepseekKey, tenant.deepseekModel || 'deepseek-chat');
          if (ds) {
            onToken(ds);
            onDone();
            resetTimeoutCount(sessionId);
            return;
          }
        } catch {}
      }
      onError(new Error(metacogTimeoutReply(sessionId)));
    } else {
      // Reset timeout count on non-timeout errors
      resetTimeoutCount(sessionId);
      onError(new Error(classifyError(err)));
    }
  }
}

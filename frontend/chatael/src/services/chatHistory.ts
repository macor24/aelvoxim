import { useAuthStore } from '../stores/authStore';

export interface BackendSession {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface BackendMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export async function fetchSessions(limit = 50): Promise<BackendSession[]> {
  const tenant = useAuthStore.getState().getActiveTenant();
  if (!tenant.apiKey) return [];
  try {
    const baseUrl = (tenant.apiUrl || 'http://8.134.185.33:9701').replace(/\/+$/, '');
    const res = await fetch(`${baseUrl}/v1/chat/sessions?limit=${limit}`, {
      headers: { Authorization: `Bearer ${tenant.apiKey}` },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.sessions || [];
  } catch {
    return [];
  }
}

export async function fetchSessionMessages(sessionId: string): Promise<BackendMessage[]> {
  const tenant = useAuthStore.getState().getActiveTenant();
  if (!tenant.apiKey) return [];
  try {
    const baseUrl = (tenant.apiUrl || 'http://8.134.185.33:9701').replace(/\/+$/, '');
    const res = await fetch(`${baseUrl}/v1/chat/sessions/${sessionId}`, {
      headers: { Authorization: `Bearer ${tenant.apiKey}` },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.messages || [];
  } catch {
    return [];
  }
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  const tenant = useAuthStore.getState().getActiveTenant();
  if (!tenant.apiKey) return false;
  try {
    const baseUrl = (tenant.apiUrl || 'http://8.134.185.33:9701').replace(/\/+$/, '');
    const res = await fetch(`${baseUrl}/v1/chat/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${tenant.apiKey}` },
    });
    return res.ok;
  } catch {
    return false;
  }
}

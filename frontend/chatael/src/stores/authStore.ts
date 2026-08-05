import { create } from 'zustand';
import type { Tenant } from '../types/auth';

// Default API URL: prefer env var at build time, then derive from the page
// origin (chatael is served by 9702, API by 9701 on the same host), and only
// fall back to localhost. Hardcoding localhost broke remote users with no
// saved tenant (browser would try to reach the user's own machine).
function defaultApiUrl(): string {
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_AELVOXIM_API_URL) {
    return import.meta.env.VITE_AELVOXIM_API_URL;
  }
  try {
    const { protocol, hostname } = window.location;
    if (hostname) return `${protocol}//${hostname}:9701`;
  } catch {}
  return 'http://localhost:9701';
}

const DEFAULT_API_URL = defaultApiUrl();

const DEFAULT_TENANT: Tenant = {
  id: 'default',
  name: 'Default',
  apiKey: '',
  apiUrl: DEFAULT_API_URL,
};

// Storage key — always use 'chatael_tenants' without isolation suffix
// Nf() in session/message stores provides per-user isolation via session/message keys
const TENANTS_KEY = 'chatael_tenants';

interface AuthState {
  tenants: Tenant[];
  activeTenantId: string;
  setActiveTenant: (id: string) => void;
  addTenant: (tenant: Tenant) => void;
  removeTenant: (id: string) => void;
  updateTenant: (id: string, updates: Partial<Tenant>) => void;
  getActiveTenant: () => Tenant;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  tenants: (() => {
    try {
      const saved = localStorage.getItem(TENANTS_KEY);
      if (saved) return JSON.parse(saved);
    } catch {}
    return [DEFAULT_TENANT];
  })(),
  activeTenantId: (() => {
    try {
      return localStorage.getItem('chatael_active_id') || 'default';
    } catch { return 'default'; }
  })(),
  setActiveTenant: (id) => {
    set({ activeTenantId: id });
    localStorage.setItem('chatael_active_id', id);
    try {
      const saved = localStorage.getItem(TENANTS_KEY);
      if (saved) set({ tenants: JSON.parse(saved) });
    } catch {}
  },
  addTenant: (tenant) => set((s) => {
    const tenants = [...s.tenants, tenant];
    localStorage.setItem(TENANTS_KEY, JSON.stringify(tenants));
    return { tenants };
  }),
  removeTenant: (id) => set((s) => {
    const tenants = s.tenants.filter((t) => t.id !== id);
    const activeTenantId = s.activeTenantId === id ? 'default' : s.activeTenantId;
    localStorage.setItem(TENANTS_KEY, JSON.stringify(tenants));
    localStorage.setItem('chatael_active_id', activeTenantId);
    return { tenants, activeTenantId };
  }),
  updateTenant: (id, updates) => set((s) => {
    const tenants = s.tenants.map((t) => (t.id === id ? { ...t, ...updates } : t));
    localStorage.setItem(TENANTS_KEY, JSON.stringify(tenants));
    return { tenants };
  }),
  getActiveTenant: () => {
    const { tenants, activeTenantId } = get();
    return tenants.find((t) => t.id === activeTenantId) || DEFAULT_TENANT;
  },
}));

// Exported for use by sessionStore and messageStore to share the same isolation key
export function getIsolationSuffix(): string {
  return storageSuffix();
}

/**
 * Resolve the active tenant's API base URL (trailing slash stripped), with a
 * sensible fallback. Single source of truth — services previously duplicated
 * this (and some read localStorage directly, risking the wrong tenant).
 */
export function getApiBase(): string {
  try {
    const active = useAuthStore.getState().getActiveTenant();
    if (active?.apiUrl) return active.apiUrl.replace(/\/+$/, '');
  } catch {}
  return DEFAULT_API_URL.replace(/\/+$/, '');
}

function storageSuffix(): string {
  try {
    const raw = localStorage.getItem('chatael_active_id');
    if (!raw || raw === 'default') return '';
    const tenantsRaw = localStorage.getItem(TENANTS_KEY);
    if (!tenantsRaw) return '';
    const tenants: Tenant[] = JSON.parse(tenantsRaw);
    const active = tenants.find((t) => t.id === raw);
    if (active?.email) return ':' + active.email.replace(/[^a-zA-Z0-9@._-]/g, '_');
  } catch {}
  return '';
}

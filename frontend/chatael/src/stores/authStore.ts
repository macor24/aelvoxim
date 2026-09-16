import { create } from 'zustand';
import type { Tenant } from '../types/auth';

// Default API URL: prefer env var at build time, then derive from the page
// origin (chatael is served by 9702, API by 9701 on the same host), and only
// fall back to localhost. Hardcoding localhost broke remote users with no
// saved tenant (browser would try to reach the user's own machine).
//
// When the app is served from a real domain (e.g. https://aelvoxim.com), use
// the page ORIGIN as-is: nginx terminates TLS on 443 and reverse-proxies /v1/*
// to the API on 9701, so no port may be appended (the API port is plain HTTP
// only — https://<domain>:9701 does not exist and fails the TLS handshake).
// Raw IP / localhost access keeps the legacy <host>:9701 behaviour.
function defaultApiUrl(): string {
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_AELVOXIM_API_URL) {
    return import.meta.env.VITE_AELVOXIM_API_URL;
  }
  try {
    const { protocol, hostname, origin } = window.location;
    if (!hostname) return 'http://localhost:9701';
    const isIpOrLocalhost = hostname === 'localhost' || /^\d{1,3}(\.\d{1,3}){3}$/.test(hostname);
    if (isIpOrLocalhost) return protocol + '//' + hostname + ':9701';
    if (origin) return origin;
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
 * Legacy API-URL hosts: saved tenants from before the domain was live may still
 * point at the raw public IP (or at the old forced :9701 port). On a real domain
 * page those values bypass nginx/HTTPS entirely — every request would hit the
 * public IP instead of the domain.
 */
const LEGACY_API_HOSTS = ['8.134.185.33'];

function migrateApiUrl(url: string): string {
  try {
    const pageHost = window.location.hostname;
    const pageIsDomain = !!pageHost
      && pageHost !== 'localhost'
      && !/^\d{1,3}(\.\d{1,3}){3}$/.test(pageHost);
    if (!pageIsDomain) return url;
    const parsed = new URL(url);
    const isLegacyHost = LEGACY_API_HOSTS.indexOf(parsed.hostname) !== -1;
    const sameHostWrongPort =
      parsed.hostname === pageHost && parsed.port !== window.location.port;
    if (isLegacyHost || sameHostWrongPort) return defaultApiUrl();
  } catch {}
  return url;
}

/**
 * Resolve the active tenant's API base URL (trailing slash stripped), with a
 * sensible fallback. Single source of truth — services previously duplicated
 * this (and some read localStorage directly, risking the wrong tenant).
 * Stale saved values are repointed at the current origin by migrateApiUrl().
 */
export function getApiBase(): string {
  try {
    const active = useAuthStore.getState().getActiveTenant();
    if (active?.apiUrl) return migrateApiUrl(active.apiUrl).replace(/\/+$/, '');
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

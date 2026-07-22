import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Eye, EyeOff, LogOut } from 'lucide-react';
import { useAuthStore } from '../../stores/authStore';
import type { Tenant } from '../../types/auth';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function AuthModal({ open, onClose }: Props) {
  const { t } = useTranslation();
  const addTenant = useAuthStore((s) => s.addTenant);
  const setActiveTenant = useAuthStore((s) => s.setActiveTenant);
  const activeTenant = useAuthStore((s) => {
    const tenants = s.tenants;
    if (!tenants || tenants.length === 0) return undefined;
    return tenants.find((t) => t.id === s.activeTenantId) || tenants[0];
  });
  const isLoggedIn = activeTenant && !!activeTenant.apiKey;
  const removeTenant = useAuthStore((s) => s.removeTenant);
  const tenants = useAuthStore((s) => s.tenants);

  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  if (!open) return null;

  // ── Logged-in view ──
  if (isLoggedIn) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
        <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-80 p-6" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-800 dark:text-gray-200">{t('common.account', '账号')}</h3>
            <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800">
              <X size={18} className="text-gray-400" />
            </button>
          </div>
          <div className="space-y-3">
            <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
              <p className="text-sm font-medium text-gray-800 dark:text-gray-200">{activeTenant!.name}</p>
              {activeTenant!.email && <p className="text-xs text-gray-400 mt-0.5">{activeTenant!.email}</p>}
              <p className="text-xs text-gray-400 mt-1 break-all font-mono">{activeTenant!.apiKey.slice(0, 16)}...</p>
            </div>

            {/* Tenant switcher */}
            {tenants.length > 1 && (
              <div>
                <label className="text-xs text-gray-500 mb-1 block">{t('common.switchAccount', '切换账号')}</label>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {tenants.map((tnt) => (
                    <div key={tnt.id}
                      className={`flex items-center justify-between px-3 py-2 rounded-lg text-sm cursor-pointer ${tnt.id === activeTenant!.id ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600' : 'hover:bg-gray-50 dark:hover:bg-gray-800'}`}
                      onClick={() => { setActiveTenant(tnt.id); onClose(); }}>
                      <span className="truncate">{tnt.name}</span>
                      {tnt.id !== activeTenant!.id && (
                        <button onClick={(e) => { e.stopPropagation(); removeTenant(tnt.id); }}
                          className="text-red-400 hover:text-red-600 text-xs">{t('common.delete', '删除')}</button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <button onClick={() => { removeTenant(activeTenant!.id); setError(''); setSuccess(''); onClose(); }}
              className="w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-red-300 dark:border-red-700 text-red-500 text-sm font-medium hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors">
              <LogOut size={15} />{t('common.logout', '退出登录')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Not logged-in: login / register tabs ──
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-80 p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div className="flex gap-1">
            {[
              { id: 'login' as const, label: t('common.login', '登录') },
              { id: 'register' as const, label: t('common.register', '注册') },
            ].map((t) => (
              <button key={t.id} onClick={() => { setTab(t.id); setError(''); setSuccess(''); }}
                className={`px-3 py-1 rounded-lg text-sm font-medium transition-colors ${tab === t.id ? 'bg-blue-600 text-white' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'}`}>
                {t.label}
              </button>
            ))}
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800">
            <X size={18} className="text-gray-400" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t('common.email', '邮箱')}</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="user@example.com"
              className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">{t('common.password', '密码')}</label>
            <div className="relative">
              <input value={password} onChange={(e) => setPassword(e.target.value)} type={showPw ? 'text' : 'password'} placeholder="******"
                className="w-full px-3 py-2 pr-10 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
              <button onClick={() => setShowPw(!showPw)} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400">
                {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
          {tab === 'register' && (
            <div>
              <label className="text-xs text-gray-500 mb-1 block">{t('common.confirmPassword', '确认密码')}</label>
              <input value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} type="password" placeholder="******"
                className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
            </div>
          )}

          {error && <div className="text-red-500 text-xs">{error}</div>}
          {success && <div className="text-green-500 text-xs">{success}</div>}

          <button onClick={tab === 'login' ? handleLogin : handleRegister} disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-40 transition-colors">
            {loading ? '...' : (tab === 'login' ? t('common.login', '登录') : t('common.register', '注册'))}
          </button>
        </div>
      </div>
    </div>
  );

  async function handleLogin() {
    if (!email || !password) { setError(t('auth.fillRequired', '请填写邮箱和密码')); return; }
    setLoading(true);
    setError('');
    try {
      const res = await fetch('http://8.134.185.33:9701/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t('auth.loginFailed', '登录失败'));
      const key = data.api_key || data.key || '';
      if (!key) throw new Error(t('auth.noKey', '未获取到 API Key'));
      const tenant: Tenant = {
        id: 'tenant:' + Date.now(),
        name: email.split('@')[0],
        email: data.email || email,
        apiKey: key,
        apiUrl: 'http://8.134.185.33:9701',
      };
      addTenant(tenant);
      setActiveTenant(tenant.id);
      setSuccess(t('auth.loginSuccess', '登录成功'));
      setTimeout(onClose, 800);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e) || t('auth.loginFailed', '登录失败'));
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister() {
    if (!email || !password) { setError(t('auth.fillRequired', '请填写邮箱和密码')); return; }
    if (password !== confirmPw) { setError(t('auth.passwordMismatch', '两次密码不一致')); return; }
    if (password.length < 6) { setError(t('auth.passwordTooShort', '密码至少6位')); return; }
    setLoading(true);
    setError('');
    try {
      const res = await fetch('http://8.134.185.33:9701/v1/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, plan: 'community' }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t('auth.registerFailed', '注册失败'));
      setSuccess(t('auth.registerSuccess', '注册成功，请登录'));
      setTab('login');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e) || t('auth.registerFailed', '注册失败'));
    } finally {
      setLoading(false);
    }
  }
}

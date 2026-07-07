import { useTranslation } from 'react-i18next';
import { Plus, User } from 'lucide-react';
import { useState } from 'react';
import { useSessionStore } from '../../stores/sessionStore';
import { useAuthStore } from '../../stores/authStore';
import SessionList from '../sidebar/SessionList';
import ThemeToggle from '../common/ThemeToggle';
import LanguageToggle from '../common/LanguageToggle';
import AuthModal from '../common/AuthModal';

export default function Sidebar() {
  const { t } = useTranslation();
  const createSession = useSessionStore((s) => s.createSession);
  const [showAuth, setShowAuth] = useState(false);
  const activeTenant = useAuthStore((s) => {
    const tenants = s.tenants;
    if (!tenants || tenants.length === 0) return s.getActiveTenant();
    return tenants.find((t) => t.id === s.activeTenantId) || tenants[0];
  });
  const isLoggedIn = activeTenant && !!activeTenant.apiKey;

  return (
    <aside className="w-72 h-screen flex flex-col bg-gray-50 dark:bg-gray-950 shrink-0">
      {/* Logo / Brand */}
      <div className="p-5 pb-3">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-9 h-9 rounded-xl bg-gradient-brand flex items-center justify-center shadow-soft">
            <span className="text-white text-lg">🧠</span>
          </div>
          <div>
            <h1 className="font-semibold text-gray-800 dark:text-gray-100">ChatAEL</h1>
            <p className="text-xs text-gray-400">Aelvoxim</p>
          </div>
        </div>
        
        {/* 新建按钮 — 渐变背景 */}
        <button 
          onClick={createSession}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl bg-gradient-brand text-white dark:text-white text-gray-900 font-medium text-sm shadow-soft hover:shadow-soft-lg hover:scale-[1.02] active:scale-[0.98] transition-all duration-200"
        >
          <Plus size={18} strokeWidth={2} />
          <span>{t('sidebar.newChat')}</span>
        </button>
      </div>
      
      <SessionList />
      
      {/* 底部用户信息 */}
      <div className="p-3">
        <div className="p-3 rounded-2xl bg-white dark:bg-gray-900 shadow-soft">
          <button 
            onClick={() => setShowAuth(true)}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <div className="w-8 h-8 rounded-full bg-gradient-brand flex items-center justify-center">
              <User size={16} className="text-white" />
            </div>
            <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
              {isLoggedIn ? activeTenant!.name : '未登录'}
            </span>
          </button>
        </div>
      </div>
      
      <AuthModal open={showAuth} onClose={() => setShowAuth(false)} />
    </aside>
  );
}

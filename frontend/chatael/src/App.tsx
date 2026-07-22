import { useEffect } from 'react';
import { useThemeStore } from './stores/themeStore';
import Sidebar from './components/layout/Sidebar';
import ChatArea from './components/chat/ChatArea';
import { onSessionConflict } from './services/apiFetch';

export default function App() {
  const mode = useThemeStore((s) => s.mode);

  // Listen for session conflict (logged in from another device)
  useEffect(() => {
    const cleanup = onSessionConflict(() => {
      alert('您的账号已在其他设备登录，当前会话已断开。请重新登录。');
      window.location.reload();
    });
    return cleanup;
  }, []);

  return (
    <div className={mode}>
      <div className="flex h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
        <Sidebar />
        <ChatArea />
      </div>
    </div>
  );
}

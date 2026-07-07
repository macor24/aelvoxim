import { useThemeStore } from './stores/themeStore';
import Sidebar from './components/layout/Sidebar';
import ChatArea from './components/chat/ChatArea';

export default function App() {
  const mode = useThemeStore((s) => s.mode);

  return (
    <div className={mode}>
      <div className="flex h-screen bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
        <Sidebar />
        <ChatArea />
      </div>
    </div>
  );
}

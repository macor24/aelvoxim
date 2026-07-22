import { Sun, Moon } from 'lucide-react';
import { useThemeStore } from '../../stores/themeStore';

export default function ThemeToggle() {
  const { mode, toggle } = useThemeStore();
  return (
    <button onClick={toggle} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400" title={mode === 'dark' ? '亮色模式' : '暗色模式'}>
      {mode === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

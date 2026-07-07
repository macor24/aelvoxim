import { create } from 'zustand';

type ThemeMode = 'light' | 'dark';

interface ThemeState {
  mode: ThemeMode;
  toggle: () => void;
}

// Initialize html class on load
const initialMode = (localStorage.getItem('theme') as ThemeMode) || 'dark';
if (initialMode === 'dark') {
  document.documentElement.classList.add('dark');
} else {
  document.documentElement.classList.remove('dark');
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: initialMode,
  toggle: () => set((s) => {
    const next = s.mode === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    document.documentElement.classList.toggle('dark');
    return { mode: next };
  }),
}));

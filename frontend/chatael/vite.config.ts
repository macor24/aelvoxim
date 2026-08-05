import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Split stable vendor deps (react, zustand, i18next, lucide) into a
    // separate cached chunk: business-code updates then only re-download the
    // small app bundle instead of the whole 246KB single file.
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'zustand', 'react-i18next', 'i18next'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Chat-history search endpoint lives on the chatael server (9702),
      // not the API server — must be matched before the generic /api rule.
      '/api/sessions/search': {
        target: 'http://127.0.0.1:9702',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:9701',
        changeOrigin: true,
      },
    },
  },
});

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3101,
    fs: { allow: ['..'] },
    proxy: {
      '/api': 'http://127.0.0.1:3100',
      '/ws': { target: 'ws://127.0.0.1:3100', ws: true },
    },
  },
  test: { environment: 'jsdom' },
});

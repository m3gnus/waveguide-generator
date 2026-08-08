import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { readFileSync } from 'node:fs';
import { lazyChunkPreloader } from './vite.config.plugins';

const productVersion = (JSON.parse(readFileSync(new URL('../shared/version.json', import.meta.url), 'utf8')) as { version: string }).version;

export default defineConfig({
  define: { __WG2_VERSION__: JSON.stringify(productVersion) },
  plugins: [react(), tailwindcss(), lazyChunkPreloader()],
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

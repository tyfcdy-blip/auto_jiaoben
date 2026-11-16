import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
const basePath = process.env.VITE_BASE_PATH || '/'

export default defineConfig({
  plugins: [vue()],
  base: basePath,
  server: {
    proxy: {
      [`${basePath.replace(/\/$/, '')}/api`]: {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    clearMocks: true,
  },
})

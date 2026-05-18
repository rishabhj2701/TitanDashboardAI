import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const appTarget = env.VITE_DEV_APP_API_TARGET || 'http://localhost:8000'
  const tileTarget = env.VITE_DEV_TILE_API_TARGET || 'http://localhost:8001'

  return {
    plugins: [react()],
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      globals: false,
      exclude: ['**/node_modules/**', '**/dist/**', 'TitanDashboardAI/**'],
    },
    server: {
      proxy: {
        '/api/roads/bbox': {
          target: tileTarget,
          changeOrigin: true,
        },
        '/tiles': {
          target: tileTarget,
          changeOrigin: true,
        },
        '/api': {
          target: appTarget,
          changeOrigin: true,
        },
      },
    },
  }
})

/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The SPA talks to the Django API on the same origin. In development, Vite proxies the API
// paths to the backend (VITE_API_PROXY, default http://localhost:8000); in production the
// nginx container does the same (frontend/nginx.conf).
const API_PATHS = ['/account/', '/blockchain/', '/admin/', '/static/', '/health/']

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_PROXY || 'http://localhost:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: Object.fromEntries(API_PATHS.map((path) => [path, { target, changeOrigin: true }])),
    },
    test: {
      environment: 'node',
    },
  }
})

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置：React 插件 + /api 代理到后端 (localhost:8000)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'
  const apiProxy = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
    },
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: apiProxy,
    },
    // Native Windows 24H mode serves the verified production bundle through
    // `vite preview`; keep its API routing identical to the development shell.
    preview: {
      port: 5173,
      strictPort: true,
      proxy: apiProxy,
    },
  }
})

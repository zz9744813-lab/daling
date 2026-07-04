import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置：React 插件 + /api 代理到后端 (localhost:8000)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})

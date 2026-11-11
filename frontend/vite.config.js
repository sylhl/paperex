import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri 期望一个固定端口的开发服务器，且不能自动切换端口
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: '127.0.0.1',
    watch: { ignored: ['**/src-tauri/**'] }
  },
  build: {
    target: 'chrome110', // Windows 上 Tauri 使用 WebView2 (Chromium)
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 4096
  }
})

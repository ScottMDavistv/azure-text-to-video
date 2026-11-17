import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Entra SPA redirect URI is bound to this exact origin in development.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // Development-only same-origin proxy, avoiding permissive API CORS or
    // browser cross-origin bearer leakage. No service tokens are injected.
    proxy: { '/projects': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
})

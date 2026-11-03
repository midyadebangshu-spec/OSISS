import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API the dev server proxies to; OSISS_API_URL=http://localhost:8001 when port 8000 is taken.
const API = process.env.OSISS_API_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: API,
        changeOrigin: true,
      },
      '/health': {
        target: API,
        changeOrigin: true,
      },
      '/data': {
        target: API,
        changeOrigin: true,
      },
    },
  },
})

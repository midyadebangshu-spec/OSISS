import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server's React refresh preamble is an inline script; relax the CSP for `vite dev` only.
const devCsp = (): Plugin => ({
  name: 'osiss-dev-csp',
  apply: 'serve',
  transformIndexHtml: (html) => html.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"),
})

// Renderer only; the Electron main and preload scripts are bundled by scripts/build-electron.mjs.
export default defineConfig({
  plugins: [react(), devCsp()],
  // Relative asset URLs so the built index.html loads from file:// inside the packaged app.
  base: './',
  server: { port: 5174, strictPort: true },
})

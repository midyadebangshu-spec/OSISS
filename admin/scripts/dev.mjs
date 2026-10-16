// Development: Vite dev server for the renderer + esbuild watch for main/preload + Electron, restarted on change.
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer } from 'vite'

import { watchElectron } from './build-electron.mjs'

const electronBinary = createRequire(import.meta.url)('electron')
const server = await createServer()
await server.listen()
const url = server.resolvedUrls.local[0]

let child = null
let restarting = false
const startElectron = () => {
  child = spawn(electronBinary, ['.'], { stdio: 'inherit', env: { ...process.env, VITE_DEV_SERVER_URL: url } })
  child.on('exit', () => {
    if (!restarting) process.exit(0)
  })
}

await watchElectron(() => {
  if (!child) return startElectron()
  restarting = true
  child.once('exit', () => {
    restarting = false
    startElectron()
  })
  child.kill()
})

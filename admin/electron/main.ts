// Electron main process: window, passkey vault, and every call to an OSISS server.
//
// The renderer has no Node access (contextIsolation, sandbox) and never sees admin tokens: it asks the main
// process to call /api/admin/* for a profile id, and the main process adds the X-Admin-Token header.

import { app, BrowserWindow, clipboard, dialog, ipcMain, safeStorage, shell } from 'electron'
import { randomUUID } from 'node:crypto'
import { existsSync, openAsBlob, readFileSync, statSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

import type {
  ApiRequest,
  ApiResponse,
  LocalStatus,
  Profile,
  ProfileInput,
  UnlockResult,
  UploadMetadata,
} from '../shared/types.ts'
import { ApiProcess, checkRepo, dockerAction, dockerStatus } from './localControl.ts'
import { adminTokenFromEnv, checkProfileUrl, isAllowedApiPath, isLoopback } from './profiles.ts'
import { MIN_PASSPHRASE_LENGTH, Vault, generateCode, type OsSealer, type StoredProfile } from './vault.ts'

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL

// Allow pointing the app at a scratch data folder (used by the smoke test).
if (process.env.OSISS_ADMIN_USER_DATA) app.setPath('userData', process.env.OSISS_ADMIN_USER_DATA)

const sealer: OsSealer = {
  // On Linux without a keyring, safeStorage falls back to a hard-coded key ("basic_text"); that protects nothing.
  isAvailable: () =>
    safeStorage.isEncryptionAvailable() &&
    !(process.platform === 'linux' && safeStorage.getSelectedStorageBackend?.() === 'basic_text'),
  encrypt: (plain) => safeStorage.encryptString(plain),
  decrypt: (sealed) => safeStorage.decryptString(sealed),
}

let vault: Vault
let window: BrowserWindow | null = null

function createWindow() {
  window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: 'OSISS Admin',
    backgroundColor: '#f8fafc',
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  window.setMenuBarVisibility(false)
  // External links open in the browser; the app window never navigates away from the app.
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (!DEV_SERVER_URL || !url.startsWith(DEV_SERVER_URL)) event.preventDefault()
  })
  if (DEV_SERVER_URL) void window.loadURL(DEV_SERVER_URL)
  else void window.loadFile(join(__dirname, '..', 'dist', 'index.html'))
}

// --- profiles ---------------------------------------------------------------------------------------------------

function publicProfile(profile: StoredProfile): Profile {
  return {
    id: profile.id,
    name: profile.name,
    url: profile.url,
    kind: profile.kind,
    repoPath: profile.repoPath,
    allowInsecureHttp: profile.allowInsecureHttp,
    hasToken: Boolean(profile.token),
  }
}

function findProfile(id: string): StoredProfile {
  const profile = vault.profiles().find((item) => item.id === id)
  if (!profile) throw new Error('Unknown server profile.')
  return profile
}

function saveProfile(input: ProfileInput): { ok: true; profile: Profile } | { ok: false; error: string } {
  const name = input.name.trim()
  if (!name) return { ok: false, error: 'Give the server a name.' }
  const kind = input.kind === 'local' ? 'local' : 'remote'
  const checked = checkProfileUrl(input.url, kind, kind === 'remote' && Boolean(input.allowInsecureHttp))
  if (!checked.ok) return checked
  if (kind === 'local' && input.repoPath) {
    const problem = checkRepo(input.repoPath)
    if (problem) return { ok: false, error: problem }
  }
  const profiles = [...vault.profiles()]
  const existing = input.id ? profiles.find((item) => item.id === input.id) : undefined
  const profile: StoredProfile = {
    id: existing?.id ?? randomUUID(),
    name,
    url: checked.url,
    kind,
    repoPath: kind === 'local' ? input.repoPath || undefined : undefined,
    allowInsecureHttp: kind === 'remote' ? Boolean(input.allowInsecureHttp) : undefined,
    token: input.token !== undefined ? input.token.trim() : (existing?.token ?? ''),
  }
  if (existing) profiles[profiles.indexOf(existing)] = profile
  else profiles.push(profile)
  vault.saveProfiles(profiles)
  return { ok: true, profile: publicProfile(profile) }
}

// --- server calls -----------------------------------------------------------------------------------------------

async function apiRequest(profileId: string, request: ApiRequest): Promise<ApiResponse> {
  const profile = findProfile(profileId)
  if (!isAllowedApiPath(request.path.split('?')[0])) {
    return { ok: false, status: 0, error: 'Path not allowed.' }
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), request.timeoutMs ?? 30_000)
  try {
    const response = await fetch(`${profile.url}${request.path}`, {
      method: request.method ?? 'GET',
      headers: {
        'X-Admin-Token': profile.token,
        ...(request.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: request.body !== undefined ? JSON.stringify(request.body) : undefined,
      signal: controller.signal,
      redirect: 'error',
    })
    return await toApiResponse(response)
  } catch (error) {
    return { ok: false, status: 0, error: networkError(error, profile.url) }
  } finally {
    clearTimeout(timer)
  }
}

async function toApiResponse(response: Response): Promise<ApiResponse> {
  const text = await response.text()
  let data: unknown = text
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    /* plain text */
  }
  if (response.ok) return { ok: true, status: response.status, data }
  const detail =
    data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : text || response.statusText
  const retryAfter = Number(response.headers.get('retry-after')) || undefined
  return {
    ok: false,
    status: response.status,
    error: typeof detail === 'string' ? detail : JSON.stringify(detail),
    retryAfter,
  }
}

function networkError(error: unknown, url: string): string {
  if (error instanceof Error && error.name === 'AbortError') return `The server at ${url} did not answer in time.`
  const cause = error instanceof Error && error.cause instanceof Error ? error.cause.message : ''
  return `Cannot reach ${url}${cause ? ` (${cause})` : ''}.`
}

async function uploadBook(profileId: string, filePath: string, metadata: UploadMetadata): Promise<ApiResponse> {
  const profile = findProfile(profileId)
  const form = new FormData()
  form.append('file', await openAsBlob(filePath, { type: 'application/pdf' }), basename(filePath))
  for (const [key, value] of Object.entries(metadata)) form.append(key, value)
  try {
    const response = await fetch(`${profile.url}/api/admin/books`, {
      method: 'POST',
      headers: { 'X-Admin-Token': profile.token },
      body: form,
      redirect: 'error',
    })
    return await toApiResponse(response)
  } catch (error) {
    return { ok: false, status: 0, error: networkError(error, profile.url) }
  }
}

async function openBookPdf(profileId: string, bookId: number) {
  const profile = findProfile(profileId)
  try {
    const response = await fetch(`${profile.url}/api/admin/books/${bookId}/pdf`, {
      headers: { 'X-Admin-Token': profile.token },
      redirect: 'error',
    })
    if (!response.ok) return { ok: false, error: (await toApiResponse(response) as { error: string }).error }
    const target = join(app.getPath('temp'), `osiss-book-${bookId}.pdf`)
    writeFileSync(target, Buffer.from(await response.arrayBuffer()))
    const error = await shell.openPath(target)
    return error ? { ok: false, error } : { ok: true }
  } catch (error) {
    return { ok: false, error: networkError(error, profile.url) }
  }
}

// --- local control ----------------------------------------------------------------------------------------------

function localProfile(profileId: string): { profile: StoredProfile; error?: string } {
  const profile = findProfile(profileId)
  if (profile.kind !== 'local' || !isLoopback(new URL(profile.url))) {
    return { profile, error: 'Process control is only available for local servers on this machine.' }
  }
  if (process.platform === 'win32') return { profile, error: 'Process control needs Linux or macOS (api.sh is a bash script).' }
  const problem = checkRepo(profile.repoPath)
  return problem ? { profile, error: problem } : { profile }
}

async function localStatus(profileId: string): Promise<LocalStatus> {
  const { profile, error } = localProfile(profileId)
  const api = new ApiProcess(app.getPath('userData'), profile.id)
  const pid = api.pid()
  let healthy = false
  try {
    const response = await fetch(`${profile.url}/health`, { signal: AbortSignal.timeout(2000) })
    healthy = response.ok
  } catch {
    healthy = false
  }
  const apiState = { running: Boolean(pid) || healthy, pid: pid ?? undefined, startedByApp: Boolean(pid), healthy, logPath: api.logFile }
  if (error) {
    return { supported: false, reason: error, repoOk: false, docker: { available: false, services: [] }, api: apiState }
  }
  return { supported: true, repoOk: true, docker: await dockerStatus(profile.repoPath!), api: apiState }
}

// --- IPC --------------------------------------------------------------------------------------------------------

function handle<A extends unknown[], R>(channel: string, fn: (...args: A) => R | Promise<R>, needsUnlock = true) {
  ipcMain.handle(channel, async (event, ...args) => {
    if (!window || event.sender !== window.webContents) throw new Error('Unknown sender')
    if (needsUnlock && !vault.isUnlocked) throw new Error('The app is locked.')
    return fn(...(args as A))
  })
}

function registerIpc() {
  handle('vault:status', () => vault.status(), false)
  handle('vault:beginSetup', () => ({ passkey: vault.beginSetup() }), false)
  handle(
    'vault:confirmSetup',
    async (passkey: string): Promise<UnlockResult> =>
      (await vault.confirmSetup(passkey))
        ? { ok: true }
        : { ok: false, error: 'That does not match the passkey shown.', lockedUntil: 0 },
    false,
  )
  handle(
    'vault:unlock',
    async (passkey: string): Promise<UnlockResult> => {
      const error = await vault.unlock(passkey)
      return error ? { ok: false, error, lockedUntil: vault.status().lockedUntil } : { ok: true }
    },
    false,
  )
  handle('vault:lock', () => vault.lock(), false)
  handle(
    'vault:changePasskey',
    async (current: string, next: { kind: 'code' } | { kind: 'passphrase'; passphrase: string }) => {
      if (next.kind === 'passphrase' && next.passphrase.length < MIN_PASSPHRASE_LENGTH) {
        return { ok: false, error: `A passphrase needs at least ${MIN_PASSPHRASE_LENGTH} characters.` }
      }
      const passkey = next.kind === 'code' ? generateCode() : next.passphrase
      const error = await vault.changePasskey(current, passkey, next.kind)
      if (error) return { ok: false, error }
      return next.kind === 'code' ? { ok: true, passkey } : { ok: true }
    },
  )
  handle('vault:setAutoLock', (minutes: number) => vault.setAutoLock(minutes))
  handle('vault:reset', () => vault.reset(), false)

  handle('profiles:list', () => vault.profiles().map(publicProfile))
  handle('profiles:save', (input: ProfileInput) => saveProfile(input))
  handle('profiles:remove', (id: string) => vault.saveProfiles(vault.profiles().filter((item) => item.id !== id)))
  handle('profiles:tokenFromEnv', (id: string) => {
    const profile = findProfile(id)
    if (!profile.repoPath) return { ok: false, error: 'Set the OSISS folder first.' }
    const envFile = join(profile.repoPath, '.env')
    if (!existsSync(envFile)) return { ok: false, error: `${envFile} does not exist. Run ./setup.sh to create it.` }
    const token = adminTokenFromEnv(readFileSync(envFile, 'utf8'))
    if (!token) return { ok: false, error: `No ADMIN_TOKEN line in ${envFile}.` }
    vault.saveProfiles(vault.profiles().map((item) => (item.id === id ? { ...item, token } : item)))
    return { ok: true }
  })

  handle('api:request', (profileId: string, request: ApiRequest) => apiRequest(profileId, request))
  handle('api:upload', (profileId: string, filePath: string, metadata: UploadMetadata) =>
    uploadBook(profileId, filePath, metadata),
  )
  handle('api:openBookPdf', (profileId: string, bookId: number) => openBookPdf(profileId, bookId))

  handle('files:pickPdf', async () => {
    const result = await dialog.showOpenDialog(window!, {
      title: 'Choose a PDF to add',
      properties: ['openFile'],
      filters: [{ name: 'PDF', extensions: ['pdf'] }],
    })
    const path = result.filePaths[0]
    return path ? { path, name: basename(path), size: statSync(path).size } : null
  })
  handle('files:pickDirectory', async () => {
    const result = await dialog.showOpenDialog(window!, { title: 'Choose the OSISS folder', properties: ['openDirectory'] })
    return result.filePaths[0] ?? null
  })

  handle('local:status', (profileId: string) => localStatus(profileId))
  handle('local:docker', (profileId: string, action: 'up' | 'stop') => {
    const { profile, error } = localProfile(profileId)
    return error ? { ok: false, output: error } : dockerAction(profile.repoPath!, action)
  })
  handle('local:api', (profileId: string, action: 'start' | 'stop') => {
    const { profile, error } = localProfile(profileId)
    if (error) return { ok: false, output: error }
    const api = new ApiProcess(app.getPath('userData'), profile.id)
    return action === 'start' ? api.start(profile.repoPath!) : api.stop()
  })
  handle('local:apiLog', (profileId: string) => new ApiProcess(app.getPath('userData'), findProfile(profileId).id).log())

  handle('clipboard:write', (text: string) => clipboard.writeText(text))
}

const singleInstance = app.requestSingleInstanceLock()
if (!singleInstance) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (window?.isMinimized()) window.restore()
    window?.focus()
  })
  app.whenReady().then(() => {
    vault = new Vault(app.getPath('userData'), sealer)
    registerIpc()
    createWindow()
  })
  app.on('window-all-closed', () => {
    vault?.lock()
    app.quit()
  })
}

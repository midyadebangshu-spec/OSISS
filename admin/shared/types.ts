// Contract between the Electron main process and the renderer (exposed as `window.osiss` by the preload).
// Server tokens never cross it: the renderer only learns whether a profile has one.

export type VaultStatus = {
  initialized: boolean
  unlocked: boolean
  /** Epoch ms until which unlocking is refused after too many wrong passkeys (0 = not locked out). */
  lockedUntil: number
  failures: number
  passkeyKind: 'code' | 'passphrase' | null
  /** Whether the vault is additionally sealed with the OS keychain (Electron safeStorage). */
  osProtected: boolean
  autoLockMinutes: number
}

export type UnlockResult = { ok: true } | { ok: false; error: string; lockedUntil: number }

export type ProfileKind = 'local' | 'remote'

export type Profile = {
  id: string
  name: string
  url: string
  kind: ProfileKind
  /** Local profiles only: the OSISS checkout on this machine (for starting Docker / the API). */
  repoPath?: string
  /** Remote profiles over plain http:// (trusted LAN only); refused unless set. */
  allowInsecureHttp?: boolean
  hasToken: boolean
}

export type ProfileInput = Omit<Profile, 'id' | 'hasToken'> & {
  id?: string
  /** New token; omit to keep the stored one. */
  token?: string
}

export type ApiRequest = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  body?: unknown
  timeoutMs?: number
}

export type ApiResponse<T = unknown> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: string; retryAfter?: number }

export type PickedFile = { path: string; name: string; size: number }

export type UploadMetadata = {
  title: string
  author: string
  publication_year: string
  department: string
}

export type LocalServiceState = {
  name: string
  state: string
  health?: string
}

export type LocalStatus = {
  supported: boolean
  reason?: string
  repoOk: boolean
  docker: { available: boolean; services: LocalServiceState[]; error?: string }
  api: { running: boolean; pid?: number; startedByApp: boolean; healthy: boolean; logPath?: string }
}

export type LocalResult = { ok: boolean; output: string }

export type OsissBridge = {
  vault: {
    status(): Promise<VaultStatus>
    /** First launch: generate a passkey to show once. Not saved until `confirmSetup`. */
    beginSetup(): Promise<{ passkey: string }>
    confirmSetup(passkey: string): Promise<UnlockResult>
    unlock(passkey: string): Promise<UnlockResult>
    lock(): Promise<void>
    /** Change to a new random code (returns it) or to a chosen passphrase (>= 10 characters). */
    changePasskey(
      current: string,
      next: { kind: 'code' } | { kind: 'passphrase'; passphrase: string },
    ): Promise<{ ok: true; passkey?: string } | { ok: false; error: string }>
    setAutoLock(minutes: number): Promise<void>
    /** Forgot passkey: wipe saved servers and start over. */
    reset(): Promise<void>
  }
  profiles: {
    list(): Promise<Profile[]>
    save(input: ProfileInput): Promise<{ ok: true; profile: Profile } | { ok: false; error: string }>
    remove(id: string): Promise<void>
    /** Read ADMIN_TOKEN from <repoPath>/.env into the profile. */
    tokenFromEnv(id: string): Promise<{ ok: boolean; error?: string }>
  }
  api: {
    request<T = unknown>(profileId: string, request: ApiRequest): Promise<ApiResponse<T>>
    upload(profileId: string, filePath: string, metadata: UploadMetadata): Promise<ApiResponse>
    openBookPdf(profileId: string, bookId: number): Promise<{ ok: boolean; error?: string }>
  }
  files: {
    pickPdf(): Promise<PickedFile | null>
    pickDirectory(): Promise<string | null>
  }
  local: {
    status(profileId: string): Promise<LocalStatus>
    docker(profileId: string, action: 'up' | 'stop'): Promise<LocalResult>
    api(profileId: string, action: 'start' | 'stop'): Promise<LocalResult>
    apiLog(profileId: string): Promise<string>
  }
  copyText(text: string): Promise<void>
}

// Passkey-locked storage for server profiles (URLs and admin tokens).
//
// The profiles are encrypted with AES-256-GCM under a key derived from the passkey with scrypt; a wrong passkey
// fails the GCM authentication, so no separate passkey hash is stored. When the OS keychain is usable (Electron
// safeStorage), the encrypted blob is sealed with it as well, so copying the vault file to another machine or user
// account is not enough to start guessing. Wrong passkeys lock the vault for a growing time.

import { createCipheriv, createDecipheriv, randomBytes, randomInt, scrypt as scryptCb } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// No 0/O, 1/I/L: easy to read and type.
export const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
export const CODE_LENGTH = 6
export const MIN_PASSPHRASE_LENGTH = 10
export const FREE_ATTEMPTS = 5
const BASE_LOCK_MS = 30_000
const MAX_LOCK_MS = 15 * 60_000
const DEFAULT_AUTO_LOCK_MINUTES = 15

// scrypt cost: ~64 MiB and a few hundred ms per attempt.
const KDF = { N: 2 ** 16, r: 8, p: 1 }

export type OsSealer = {
  isAvailable(): boolean
  encrypt(plain: string): Buffer
  decrypt(sealed: Buffer): string
}

export type StoredProfile = {
  id: string
  name: string
  url: string
  kind: 'local' | 'remote'
  repoPath?: string
  allowInsecureHttp?: boolean
  token: string
}

type VaultData = { profiles: StoredProfile[] }

type VaultFile = {
  version: 1
  passkeyKind: 'code' | 'passphrase'
  kdf: { salt: string; N: number; r: number; p: number }
  osProtected: boolean
  payload: string
  autoLockMinutes: number
}

type Lockout = { failures: number; lockedUntil: number }

export function generateCode(): string {
  let code = ''
  for (let i = 0; i < CODE_LENGTH; i += 1) code += CODE_ALPHABET[randomInt(CODE_ALPHABET.length)]
  return code
}

/** Codes are compared case-insensitively and without spaces or dashes ("k7m-q3x" == "K7MQ3X"). */
export function normalizeCode(input: string): string {
  return input.toUpperCase().replace(/[\s-]/g, '')
}

export function lockDuration(failures: number): number {
  if (failures < FREE_ATTEMPTS) return 0
  return Math.min(BASE_LOCK_MS * 2 ** (failures - FREE_ATTEMPTS), MAX_LOCK_MS)
}

function deriveKey(passkey: string, salt: Buffer, kdf: { N: number; r: number; p: number }): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scryptCb(passkey.normalize('NFC'), salt, 32, { ...kdf, maxmem: 256 * 1024 * 1024 }, (error, key) =>
      error ? reject(error) : resolve(key),
    )
  })
}

function encrypt(key: Buffer, data: VaultData): string {
  const iv = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key, iv)
  const body = Buffer.concat([cipher.update(JSON.stringify(data), 'utf8'), cipher.final()])
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString('base64')
}

function decrypt(key: Buffer, payload: string): VaultData {
  const raw = Buffer.from(payload, 'base64')
  const decipher = createDecipheriv('aes-256-gcm', key, raw.subarray(0, 12))
  decipher.setAuthTag(raw.subarray(12, 28))
  const plain = Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString('utf8')
  return JSON.parse(plain) as VaultData
}

export class Vault {
  private readonly file: string
  private readonly lockoutFile: string
  private key: Buffer | null = null
  private data: VaultData | null = null
  private pendingCode: string | null = null
  private readonly dir: string
  private readonly sealer: OsSealer | null
  private readonly now: () => number

  constructor(dir: string, sealer: OsSealer | null, now: () => number = Date.now) {
    this.dir = dir
    this.sealer = sealer
    this.now = now
    this.file = join(dir, 'vault.json')
    this.lockoutFile = join(dir, 'vault-lockout.json')
  }

  private read(): VaultFile | null {
    if (!existsSync(this.file)) return null
    return JSON.parse(readFileSync(this.file, 'utf8')) as VaultFile
  }

  private writeAtomic(path: string, content: string) {
    mkdirSync(this.dir, { recursive: true })
    const tmp = `${path}.tmp`
    writeFileSync(tmp, content, { mode: 0o600 })
    renameSync(tmp, path)
  }

  private lockout(): Lockout {
    try {
      return JSON.parse(readFileSync(this.lockoutFile, 'utf8')) as Lockout
    } catch {
      return { failures: 0, lockedUntil: 0 }
    }
  }

  private setLockout(value: Lockout) {
    this.writeAtomic(this.lockoutFile, JSON.stringify(value))
  }

  status() {
    const file = this.read()
    const lockout = this.lockout()
    return {
      initialized: file !== null,
      unlocked: this.key !== null,
      lockedUntil: lockout.lockedUntil > this.now() ? lockout.lockedUntil : 0,
      failures: lockout.failures,
      passkeyKind: file?.passkeyKind ?? null,
      osProtected: file?.osProtected ?? false,
      autoLockMinutes: file?.autoLockMinutes ?? DEFAULT_AUTO_LOCK_MINUTES,
    }
  }

  get isUnlocked(): boolean {
    return this.key !== null
  }

  beginSetup(): string {
    if (this.read()) throw new Error('The app already has a passkey.')
    this.pendingCode = generateCode()
    return this.pendingCode
  }

  async confirmSetup(typed: string): Promise<boolean> {
    if (!this.pendingCode || this.read()) return false
    if (normalizeCode(typed) !== this.pendingCode) return false
    await this.create(this.pendingCode, 'code', { profiles: [] }, DEFAULT_AUTO_LOCK_MINUTES)
    this.pendingCode = null
    return true
  }

  private async create(
    passkey: string,
    kind: 'code' | 'passphrase',
    data: VaultData,
    autoLockMinutes: number,
  ): Promise<void> {
    const salt = randomBytes(16)
    const key = await deriveKey(passkey, salt, KDF)
    const osProtected = Boolean(this.sealer?.isAvailable())
    const file: VaultFile = {
      version: 1,
      passkeyKind: kind,
      kdf: { salt: salt.toString('base64'), ...KDF },
      osProtected,
      payload: '',
      autoLockMinutes,
    }
    this.key = key
    this.data = data
    this.persist(file)
    this.setLockout({ failures: 0, lockedUntil: 0 })
  }

  private persist(file: VaultFile) {
    if (!this.key || !this.data) throw new Error('Vault is locked.')
    const encrypted = encrypt(this.key, this.data)
    file.payload = file.osProtected && this.sealer ? this.sealer.encrypt(encrypted).toString('base64') : encrypted
    this.writeAtomic(this.file, JSON.stringify(file, null, 2))
  }

  /** Returns null on success, or an error message. */
  async unlock(typed: string): Promise<string | null> {
    const file = this.read()
    if (!file) return 'The app has not been set up yet.'
    const lockout = this.lockout()
    if (lockout.lockedUntil > this.now()) return 'Too many wrong passkeys. Wait before trying again.'

    const passkey = file.passkeyKind === 'code' ? normalizeCode(typed) : typed
    let encrypted = file.payload
    if (file.osProtected) {
      if (!this.sealer?.isAvailable()) return 'The OS keychain that protects this vault is not available.'
      try {
        encrypted = this.sealer.decrypt(Buffer.from(file.payload, 'base64'))
      } catch {
        return 'The OS keychain could not open the vault (different user or machine?). Reset the app to start over.'
      }
    }
    const key = await deriveKey(passkey, Buffer.from(file.kdf.salt, 'base64'), file.kdf)
    try {
      this.data = decrypt(key, encrypted)
    } catch {
      const failures = lockout.failures + 1
      this.setLockout({ failures, lockedUntil: this.now() + lockDuration(failures) })
      return 'Wrong passkey.'
    }
    this.key = key
    this.setLockout({ failures: 0, lockedUntil: 0 })
    return null
  }

  lock() {
    this.key?.fill(0)
    this.key = null
    this.data = null
  }

  /** Re-encrypt under a new passkey after checking the current one. */
  async changePasskey(current: string, next: string, kind: 'code' | 'passphrase'): Promise<string | null> {
    const error = await this.unlock(current)
    if (error) return error
    if (kind === 'passphrase' && next.length < MIN_PASSPHRASE_LENGTH) {
      return `A passphrase needs at least ${MIN_PASSPHRASE_LENGTH} characters.`
    }
    const file = this.read()!
    await this.create(next, kind, this.data!, file.autoLockMinutes)
    return null
  }

  setAutoLock(minutes: number) {
    const file = this.read()
    if (!file || !this.isUnlocked) throw new Error('Vault is locked.')
    file.autoLockMinutes = Math.min(Math.max(Math.round(minutes), 1), 240)
    this.persist(file)
  }

  reset() {
    this.lock()
    this.pendingCode = null
    rmSync(this.file, { force: true })
    rmSync(this.lockoutFile, { force: true })
  }

  profiles(): StoredProfile[] {
    if (!this.data) throw new Error('Vault is locked.')
    return this.data.profiles
  }

  saveProfiles(profiles: StoredProfile[]) {
    const file = this.read()
    if (!file || !this.data) throw new Error('Vault is locked.')
    this.data = { profiles }
    this.persist(file)
  }
}

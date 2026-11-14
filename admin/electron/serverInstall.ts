// Installing an OSISS server on this computer by running the project's install.sh (no Electron imports, so it is
// testable in plain Node).
//
// Steps: download the script, ask it whether anything needs administrator rights (--check), run that one step
// through pkexec (Linux), then run the rest as the current user. The administrator step gets the script text as an
// argument of `bash -c` instead of a file path, so nothing a user process could swap sits on disk between the
// permission prompt and the run. Nothing is sent to the renderer except the log and the state.

import { spawn as nodeSpawn, type ChildProcess, type SpawnOptions } from 'node:child_process'
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs'
import { delimiter, join } from 'node:path'

import { INSTALL_SCRIPT_URL, serverInstallCommand, validateInstallDir } from '../shared/install.ts'
import type { InstallStatus } from '../shared/types.ts'

export const MAX_SCRIPT_BYTES = 256 * 1024
const EXIT_ROOT_NEEDED = 10
const EXIT_UNSUPPORTED = 20
const LOG_LIMIT = 60_000

export function looksLikeInstallScript(text: string): boolean {
  return text.startsWith('#!/usr/bin/env bash') && text.includes('OSISS server installer') && text.length <= MAX_SCRIPT_BYTES
}

export function platformReason(platform: string): string | null {
  if (platform === 'linux' || platform === 'darwin') return null
  if (platform === 'win32') {
    return 'The OSISS server does not run on Windows. Install it on Ubuntu/Debian or macOS, then add it here as a server on another machine.'
  }
  return 'The server installer supports Ubuntu/Debian and macOS.'
}

/** A GUI-launched app has a short PATH (no Homebrew on macOS, no sbin on Linux); the installer needs them. */
export function childEnv(platform: string, env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const extra = platform === 'darwin' ? ['/opt/homebrew/bin', '/opt/homebrew/sbin', '/usr/local/bin'] : ['/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/sbin']
  const have = (env.PATH ?? '').split(delimiter).filter(Boolean)
  return { ...env, PATH: [...have, ...extra.filter((dir) => !have.includes(dir))].join(delimiter) }
}

type Spawner = (command: string, args: string[], options: SpawnOptions) => ChildProcess

export type InstallerDeps = {
  platform: string
  user: string
  home: string
  dataDir: string
  env: NodeJS.ProcessEnv
  fetchScript: () => Promise<string>
  spawn?: Spawner
  /** Whether a program (pkexec) can be found. */
  hasCommand?: (name: string) => boolean
}

export class ServerInstaller {
  private state: InstallStatus['state'] = 'idle'
  private step = ''
  private log = ''
  private dir: string | undefined
  private missing: string[] = []
  private message: string | undefined
  private manualCommand: string | undefined
  private child: ChildProcess | null = null
  private cancelRequested = false
  private elevated = false
  profileId: string | undefined

  private readonly deps: InstallerDeps

  constructor(deps: InstallerDeps) {
    this.deps = deps
  }

  private get spawn(): Spawner {
    return this.deps.spawn ?? (nodeSpawn as Spawner)
  }

  defaultDir(): string {
    return join(this.deps.home, 'osiss')
  }

  status(): InstallStatus {
    const reason = platformReason(this.deps.platform)
    return {
      supported: reason === null,
      reason: reason ?? undefined,
      defaultDir: this.defaultDir(),
      scriptUrl: INSTALL_SCRIPT_URL,
      state: this.state,
      step: this.step,
      canCancel: this.state === 'running' && !this.elevated,
      log: this.log,
      dir: this.dir,
      profileId: this.profileId,
      missing: this.missing,
      message: this.message,
      manualCommand: this.manualCommand,
    }
  }

  private append(text: string) {
    this.log += text
    if (this.log.length > LOG_LIMIT) this.log = this.log.slice(-LOG_LIMIT)
  }

  private note(text: string) {
    this.append(`\n== ${text}\n`)
  }

  start(dir: string): { ok: boolean; error?: string } {
    const reason = platformReason(this.deps.platform)
    if (reason) return { ok: false, error: reason }
    if (this.state === 'running') return { ok: false, error: 'An installation is already running.' }
    const problem = validateInstallDir(dir)
    if (problem) return { ok: false, error: problem }
    this.state = 'running'
    this.step = 'Starting'
    this.log = ''
    this.dir = dir.trim().replace(/\/+$/, '') || '/'
    this.missing = []
    this.message = undefined
    this.manualCommand = undefined
    this.profileId = undefined
    this.cancelRequested = false
    this.elevated = false
    void this.run(this.dir)
    return { ok: true }
  }

  cancel(): void {
    if (this.state !== 'running' || this.elevated) return
    this.cancelRequested = true
    this.stopChild()
  }

  private stopChild() {
    const child = this.child
    if (!child) return
    if (child.pid) {
      try {
        process.kill(-child.pid, 'SIGTERM') // the script and everything it started
        return
      } catch {
        /* not a process group leader: fall through */
      }
    }
    child.kill('SIGTERM')
  }

  private exec(command: string, args: string[], options: { capture?: boolean } = {}): Promise<{ code: number; stdout: string }> {
    return new Promise((resolve) => {
      let stdout = ''
      const child = this.spawn(command, args, {
        cwd: this.deps.dataDir,
        env: childEnv(this.deps.platform, this.deps.env),
        detached: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      this.child = child
      const onData = (chunk: Buffer | string, capture: boolean) => {
        const text = String(chunk)
        if (capture) stdout += text
        else this.append(text)
      }
      child.stdout?.on('data', (chunk) => onData(chunk, Boolean(options.capture)))
      child.stderr?.on('data', (chunk) => onData(chunk, false))
      child.on('error', (error) => {
        this.child = null
        this.append(`${error.message}\n`)
        resolve({ code: 127, stdout })
      })
      child.on('close', (code) => {
        this.child = null
        resolve({ code: code ?? 1, stdout })
      })
    })
  }

  private args(dir: string): string[] {
    return ['--dir', dir, '--user', this.deps.user, '--listen', 'local', '--no-print-token']
  }

  private fail(message: string) {
    this.state = 'failed'
    this.message = message
    this.step = 'Failed'
  }

  private block(message: string, manual?: string) {
    this.state = 'blocked'
    this.message = message
    this.manualCommand = manual
    this.step = 'Waiting for you'
  }

  private async run(dir: string): Promise<void> {
    try {
      this.step = 'Downloading the installer'
      this.note(`Downloading ${INSTALL_SCRIPT_URL}`)
      let script: string
      try {
        script = await this.deps.fetchScript()
      } catch (error) {
        return this.fail(`Could not download the installer: ${error instanceof Error ? error.message : String(error)}. Check the internet connection.`)
      }
      if (!looksLikeInstallScript(script)) return this.fail('The downloaded file does not look like the OSISS installer, so it was not run.')
      mkdirSync(this.deps.dataDir, { recursive: true })
      const file = join(this.deps.dataDir, 'install-server.sh')
      writeFileSync(file, script, { mode: 0o700 })
      chmodSync(file, 0o700)

      this.step = 'Checking this computer'
      this.note('Checking what is missing')
      const check = await this.exec('/bin/bash', [file, '--check', ...this.args(dir)], { capture: true })
      if (this.cancelRequested) return this.cancelled()
      const listed = check.stdout.split('\n').map((line) => line.trim()).filter(Boolean)
      if (check.code === EXIT_UNSUPPORTED) {
        this.missing = listed
        return this.block(listed.length ? 'Install these first, then press Check again.' : 'This computer is not supported by the installer.')
      }
      if (check.code === EXIT_ROOT_NEEDED) {
        this.missing = listed
        const done = await this.administratorStep(script, dir, listed)
        if (!done) return
      } else if (check.code !== 0) {
        return this.fail(`The check failed (exit code ${check.code}). See the log.`)
      }

      this.step = 'Installing the server'
      this.note('Installing: this downloads about 12 GB of models and can take 10 to 30 minutes')
      const result = await this.exec('/bin/bash', [file, '--phase', 'user', ...this.args(dir)])
      if (this.cancelRequested) return this.cancelled()
      if (result.code !== 0) return this.fail(`The installer stopped (exit code ${result.code}). See the log above; you can fix the problem and run it again.`)
      this.state = 'done'
      this.step = 'Done'
      this.note('Installed')
    } catch (error) {
      this.fail(error instanceof Error ? error.message : String(error))
    }
  }

  private cancelled() {
    this.state = 'failed'
    this.step = 'Cancelled'
    this.message = 'Cancelled. Run it again to continue; finished steps are kept.'
  }

  /** The one step that needs administrator rights. Returns true when it is done (or not needed). */
  private async administratorStep(script: string, dir: string, reasons: string[]): Promise<boolean> {
    const manual = serverInstallCommand()
    if (this.deps.platform !== 'linux') {
      this.block('Administrator rights are needed on this system; run the installer in a terminal.', manual)
      return false
    }
    const has = this.deps.hasCommand ?? (() => true)
    if (!has('pkexec')) {
      this.block('This computer has no graphical permission prompt (pkexec). Run the installer in a terminal instead, then press Check again.', manual)
      return false
    }
    this.step = 'Waiting for administrator permission'
    this.note(`Administrator rights are needed for: ${reasons.join('; ')}`)
    this.elevated = true
    const result = await this.exec('pkexec', ['/bin/bash', '-c', script, 'bash', '--phase', 'root', ...this.args(dir)])
    this.elevated = false
    if (result.code === 126 || result.code === 127) {
      this.block('Administrator permission was not granted. Press Start to try again, or run the installer in a terminal.', manual)
      return false
    }
    if (result.code !== 0) {
      this.fail(`The administrator step failed (exit code ${result.code}). See the log.`)
      return false
    }
    return true
  }
}

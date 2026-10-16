// Starting and stopping the OSISS services on this machine (local profiles only).
//
// Docker services go through `docker compose` with the repo's docker-compose.yml. The API is started with the
// repo's api.sh as a detached process group, so it keeps running when the admin app closes; its PID and output
// go to files in the app's data folder so a later session can still stop it.

import { spawn } from 'node:child_process'
import { closeSync, existsSync, openSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { LocalResult, LocalServiceState } from '../shared/types.ts'

const COMPOSE_SERVICES = ['postgres', 'elasticsearch']

export function checkRepo(repoPath: string | undefined): string | null {
  if (!repoPath) return 'Set the OSISS folder for this server first.'
  for (const file of ['api.sh', 'docker-compose.yml', join('src', 'api_server.py')]) {
    if (!existsSync(join(repoPath, file))) return `${repoPath} does not look like the OSISS folder (missing ${file}).`
  }
  return null
}

function run(command: string, args: string[], cwd: string, timeoutMs = 300_000): Promise<LocalResult> {
  return new Promise((resolve) => {
    let output = ''
    const child = spawn(command, args, { cwd, env: process.env })
    const timer = setTimeout(() => child.kill('SIGTERM'), timeoutMs)
    child.stdout.on('data', (chunk) => (output += chunk))
    child.stderr.on('data', (chunk) => (output += chunk))
    child.on('error', (error) => {
      clearTimeout(timer)
      resolve({ ok: false, output: `${output}${error.message}` })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ ok: code === 0, output: output.trim() })
    })
  })
}

export function parseComposePs(output: string): LocalServiceState[] {
  // `docker compose ps --format json` prints one JSON object per line (newer) or one array (older).
  const text = output.trim()
  if (!text) return []
  const rows: Array<Record<string, string>> = text.startsWith('[')
    ? JSON.parse(text)
    : text.split('\n').filter((line) => line.trim().startsWith('{')).map((line) => JSON.parse(line))
  return rows.map((row) => ({ name: row.Service ?? row.Name, state: row.State, health: row.Health || undefined }))
}

export async function dockerStatus(repoPath: string) {
  const result = await run('docker', ['compose', '-f', join(repoPath, 'docker-compose.yml'), 'ps', '--all', '--format', 'json'], repoPath, 20_000)
  if (!result.ok) return { available: false, services: [], error: result.output || 'docker is not available' }
  try {
    return { available: true, services: parseComposePs(result.output) }
  } catch {
    return { available: true, services: [], error: 'Could not read docker compose output.' }
  }
}

export function dockerAction(repoPath: string, action: 'up' | 'stop'): Promise<LocalResult> {
  const file = join(repoPath, 'docker-compose.yml')
  const args = action === 'up' ? ['compose', '-f', file, 'up', '-d', ...COMPOSE_SERVICES] : ['compose', '-f', file, 'stop', ...COMPOSE_SERVICES]
  return run('docker', args, repoPath)
}

export class ApiProcess {
  private readonly pidFile: string
  readonly logFile: string

  constructor(dataDir: string, profileId: string) {
    this.pidFile = join(dataDir, `api-${profileId}.pid`)
    this.logFile = join(dataDir, `api-${profileId}.log`)
  }

  /** PID of an API this app started that is still alive (and still uvicorn, where /proc allows checking). */
  pid(): number | null {
    if (!existsSync(this.pidFile)) return null
    const pid = Number(readFileSync(this.pidFile, 'utf8').trim())
    if (!Number.isInteger(pid) || pid <= 0) return null
    try {
      process.kill(pid, 0)
    } catch {
      rmSync(this.pidFile, { force: true })
      return null
    }
    const cmdline = `/proc/${pid}/cmdline`
    // Before api.sh execs uvicorn (e.g. while it installs missing modules) the process is still `bash api.sh`.
    const command = existsSync(cmdline) ? readFileSync(cmdline, 'utf8') : 'uvicorn'
    if (!command.includes('uvicorn') && !command.includes('api.sh')) {
      rmSync(this.pidFile, { force: true })
      return null
    }
    return pid
  }

  start(repoPath: string): LocalResult {
    if (this.pid()) return { ok: false, output: 'The API is already running (started from this app).' }
    const log = openSync(this.logFile, 'w')
    try {
      // exec in api.sh makes the bash PID become uvicorn's, so the stored PID stays valid.
      const child = spawn('bash', [join(repoPath, 'api.sh')], {
        cwd: repoPath,
        detached: true,
        stdio: ['ignore', log, log],
        env: process.env,
      })
      if (!child.pid) return { ok: false, output: 'Could not start api.sh.' }
      writeFileSync(this.pidFile, String(child.pid))
      child.unref()
      return { ok: true, output: `Started api.sh (PID ${child.pid}). Loading models can take a minute.` }
    } finally {
      closeSync(log)
    }
  }

  async stop(): Promise<LocalResult> {
    const pid = this.pid()
    if (!pid) return { ok: false, output: 'No API started from this app is running. Stop other instances where they were started.' }
    const signal = (sig: NodeJS.Signals) => {
      try {
        process.kill(-pid, sig) // the whole process group
      } catch {
        try {
          process.kill(pid, sig)
        } catch {
          /* already gone */
        }
      }
    }
    signal('SIGTERM')
    for (let i = 0; i < 50; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 200))
      if (!this.pid()) return { ok: true, output: 'API stopped.' }
    }
    signal('SIGKILL')
    rmSync(this.pidFile, { force: true })
    return { ok: true, output: 'API did not exit in 10 s and was killed.' }
  }

  log(maxBytes = 64_000): string {
    if (!existsSync(this.logFile)) return ''
    const text = readFileSync(this.logFile, 'utf8')
    return text.length > maxBytes ? text.slice(-maxBytes) : text
  }
}

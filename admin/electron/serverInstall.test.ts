import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { ServerInstaller, childEnv, looksLikeInstallScript, platformReason } from './serverInstall.ts'

const SCRIPT = '#!/usr/bin/env bash\n# OSISS server installer\nexit 0\n'

type Step = { code: number; stdout?: string; stderr?: string; hold?: boolean }
type Call = { command: string; args: string[] }

function harness(plan: (call: Call) => Step, overrides: { platform?: string; hasCommand?: boolean; script?: string } = {}) {
  const calls: Call[] = []
  const children: EventEmitter[] = []
  const spawn = (command: string, args: string[]) => {
    const call = { command, args }
    calls.push(call)
    const step = plan(call)
    const child = new EventEmitter() as EventEmitter & { stdout: EventEmitter; stderr: EventEmitter; pid?: number; kill: () => void }
    child.stdout = new EventEmitter()
    child.stderr = new EventEmitter()
    child.kill = () => setImmediate(() => child.emit('close', 143))
    children.push(child)
    if (!step.hold) {
      setImmediate(() => {
        if (step.stdout) child.stdout.emit('data', Buffer.from(step.stdout))
        if (step.stderr) child.stderr.emit('data', Buffer.from(step.stderr))
        child.emit('close', step.code)
      })
    }
    return child as never
  }
  const installer = new ServerInstaller({
    platform: overrides.platform ?? 'linux',
    user: 'alice',
    home: '/home/alice',
    dataDir: mkdtempSync(join(tmpdir(), 'osiss-install-test-')),
    env: { PATH: '/usr/bin:/bin' },
    fetchScript: async () => overrides.script ?? SCRIPT,
    spawn,
    hasCommand: () => overrides.hasCommand ?? true,
  })
  return { installer, calls, children }
}

async function finished(installer: ServerInstaller, wanted?: string) {
  for (let i = 0; i < 200; i += 1) {
    const { state } = installer.status()
    if (state !== 'running' && (!wanted || state === wanted)) return installer.status()
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error(`still ${installer.status().state}`)
}

const isCheck = (call: Call) => call.args.includes('--check')
const isUser = (call: Call) => call.args.includes('user') && call.args.includes('--phase')

test('Windows is refused with a pointer to remote servers', () => {
  const { installer } = harness(() => ({ code: 0 }), { platform: 'win32' })
  const result = installer.start('/home/alice/osiss')
  assert.equal(result.ok, false)
  assert.match(result.error ?? '', /does not run on Windows/)
  assert.equal(installer.status().supported, false)
  assert.equal(platformReason('linux'), null)
  assert.equal(platformReason('darwin'), null)
})

test('a bad folder is refused before anything runs', () => {
  const { installer, calls } = harness(() => ({ code: 0 }))
  for (const dir of ['', 'relative', '/usr', '/']) assert.equal(installer.start(dir).ok, false, dir)
  assert.equal(calls.length, 0)
})

test('a downloaded file that is not the installer is never run', async () => {
  const { installer, calls } = harness(() => ({ code: 0 }), { script: '#!/bin/sh\nrm -rf /\n' })
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'failed')
  assert.match(status.message ?? '', /does not look like the OSISS installer/)
  assert.equal(calls.length, 0)
  assert.equal(looksLikeInstallScript(SCRIPT), true)
  assert.equal(looksLikeInstallScript('#!/usr/bin/env bash\necho hi\n'), false)
})

test('nothing missing: check, then the user step, then done', async () => {
  const { installer, calls } = harness((call) => (isCheck(call) ? { code: 0 } : { code: 0, stdout: 'installing\n' }))
  assert.equal(installer.start('/home/alice/osiss').ok, true)
  assert.equal(installer.start('/home/alice/osiss').ok, false) // already running
  const status = await finished(installer)
  assert.equal(status.state, 'done')
  assert.equal(status.dir, '/home/alice/osiss')
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[0]!.args.slice(1), ['--check', '--dir', '/home/alice/osiss', '--user', 'alice', '--listen', 'local', '--no-print-token'])
  assert.ok(isUser(calls[1]!) && calls[1]!.args.includes('--no-print-token'))
  assert.ok(calls.every((call) => call.command !== 'pkexec'))
  assert.match(status.log, /installing/)
})

test('missing system pieces: one pkexec step with the script as the bash -c text, then the user step', async () => {
  const { installer, calls } = harness((call) => (isCheck(call) ? { code: 10, stdout: 'Docker with the Compose plugin\nNode.js 22\n' } : { code: 0 }))
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'done')
  const elevated = calls.find((call) => call.command === 'pkexec')!
  assert.deepEqual(elevated.args.slice(0, 2), ['/bin/bash', '-c'])
  assert.equal(elevated.args[2], SCRIPT) // the text itself, not a path a user process could swap
  assert.deepEqual(elevated.args.slice(3, 6), ['bash', '--phase', 'root'])
  assert.deepEqual(status.missing, ['Docker with the Compose plugin', 'Node.js 22'])
  assert.equal(calls.indexOf(elevated) < calls.findIndex(isUser), true)
})

test('the administrator step cannot be cancelled, the rest can', async () => {
  let release: (() => void) | undefined
  const { installer, children } = harness((call) => {
    if (isCheck(call)) return { code: 10, stdout: 'Docker\n' }
    return call.command === 'pkexec' ? { code: 0, hold: true } : { code: 0, hold: true }
  })
  installer.start('/home/alice/osiss')
  for (let i = 0; i < 100 && children.length < 2; i += 1) await new Promise((resolve) => setTimeout(resolve, 5))
  assert.equal(installer.status().canCancel, false)
  installer.cancel() // ignored during the administrator step
  assert.equal(installer.status().state, 'running')
  children[1]!.emit('close', 0) // permission given, step done
  for (let i = 0; i < 100 && children.length < 3; i += 1) await new Promise((resolve) => setTimeout(resolve, 5))
  assert.equal(installer.status().canCancel, true)
  installer.cancel()
  const status = await finished(installer)
  assert.equal(status.state, 'failed')
  assert.equal(status.step, 'Cancelled')
  release?.()
})

test('without pkexec the app hands over a terminal command', async () => {
  const { installer, calls } = harness((call) => (isCheck(call) ? { code: 10, stdout: 'Docker\n' } : { code: 0 }), { hasCommand: false })
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'blocked')
  assert.match(status.manualCommand ?? '', /^curl -fsSL https:\/\/.*install\.sh \| bash/)
  assert.equal(calls.length, 1)
})

test('refusing the permission prompt blocks (not fails) so it can be retried', async () => {
  const { installer } = harness((call) => (isCheck(call) ? { code: 10, stdout: 'Docker\n' } : call.command === 'pkexec' ? { code: 126 } : { code: 0 }))
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'blocked')
  assert.match(status.message ?? '', /permission was not granted/)
  assert.equal(installer.start('/home/alice/osiss').ok, true) // retry allowed
  await finished(installer)
})

test('macOS: missing Homebrew or Docker Desktop is listed, no administrator step is attempted', async () => {
  const { installer, calls } = harness(() => ({ code: 20, stdout: 'Homebrew (https://brew.sh)\nDocker Desktop (https://www.docker.com/products/docker-desktop/), installed and started\n' }), { platform: 'darwin' })
  installer.start('/Users/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'blocked')
  assert.equal(status.missing.length, 2)
  assert.equal(calls.length, 1)
})

test('a failing user step is reported with the log kept', async () => {
  const { installer } = harness((call) => (isCheck(call) ? { code: 0 } : { code: 1, stderr: 'docker: permission denied\n' }))
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'failed')
  assert.match(status.message ?? '', /exit code 1/)
  assert.match(status.log, /permission denied/)
})

test('a failing administrator step stops before the user step', async () => {
  const { installer, calls } = harness((call) => (isCheck(call) ? { code: 10, stdout: 'Docker\n' } : call.command === 'pkexec' ? { code: 1 } : { code: 0 }))
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.equal(status.state, 'failed')
  assert.equal(calls.some(isUser), false)
})

test('the log is capped', async () => {
  const { installer } = harness((call) => (isCheck(call) ? { code: 0 } : { code: 0, stdout: 'x'.repeat(200_000) }))
  installer.start('/home/alice/osiss')
  const status = await finished(installer)
  assert.ok(status.log.length <= 60_000)
})

test('the installer gets the folders a GUI-launched app lacks on PATH', () => {
  assert.match(childEnv('darwin', { PATH: '/usr/bin:/bin' }).PATH ?? '', /\/opt\/homebrew\/bin/)
  assert.match(childEnv('linux', { PATH: '/usr/bin' }).PATH ?? '', /\/usr\/sbin/)
  const unchanged = childEnv('linux', { PATH: '/usr/sbin:/sbin:/usr/local/sbin:/usr/local/bin' }).PATH
  assert.equal(unchanged, '/usr/sbin:/sbin:/usr/local/sbin:/usr/local/bin') // no duplicates
})

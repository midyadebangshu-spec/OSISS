import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { ApiProcess, checkRepo, parseComposePs } from './localControl.ts'

function fakeRepo(script: string) {
  const repo = mkdtempSync(join(tmpdir(), 'osiss-repo-'))
  mkdirSync(join(repo, 'src'))
  writeFileSync(join(repo, 'src', 'api_server.py'), '')
  writeFileSync(join(repo, 'docker-compose.yml'), '')
  writeFileSync(join(repo, 'api.sh'), script)
  return repo
}

test('checkRepo wants an OSISS checkout', () => {
  assert.match(checkRepo(undefined) ?? '', /Set the OSISS folder/)
  assert.match(checkRepo(tmpdir()) ?? '', /missing api.sh/)
  assert.equal(checkRepo(fakeRepo('')), null)
})

test('docker compose ps output in both formats', () => {
  const line = '{"Service":"postgres","State":"running","Health":"healthy"}\n{"Service":"elasticsearch","State":"exited","Health":""}'
  assert.deepEqual(parseComposePs(line), [
    { name: 'postgres', state: 'running', health: 'healthy' },
    { name: 'elasticsearch', state: 'exited', health: undefined },
  ])
  assert.deepEqual(parseComposePs('[{"Service":"postgres","State":"running"}]'), [{ name: 'postgres', state: 'running', health: undefined }])
  assert.deepEqual(parseComposePs(''), [])
})

test('the API is started detached, survives as a tracked PID, and stops', { timeout: 20_000 }, async () => {
  // Like api.sh, the script execs the server (here a sleep named "uvicorn").
  const repo = fakeRepo('#!/usr/bin/env bash\necho starting\nexec -a uvicorn sleep 60\n')
  const data = mkdtempSync(join(tmpdir(), 'osiss-data-'))
  const api = new ApiProcess(data, 'p1')
  assert.equal(api.pid(), null)

  const started = api.start(repo)
  assert.equal(started.ok, true, started.output)
  await new Promise((resolve) => setTimeout(resolve, 300))
  const pid = api.pid()
  assert.ok(pid)
  assert.equal(api.start(repo).ok, false) // already running
  assert.match(api.log(), /starting/)

  // A new ApiProcess (e.g. after the app restarts) still finds and stops it.
  const later = new ApiProcess(data, 'p1')
  assert.equal(later.pid(), pid)
  assert.equal((await later.stop()).ok, true)
  assert.equal(later.pid(), null)
  assert.equal((await later.stop()).ok, false)
})

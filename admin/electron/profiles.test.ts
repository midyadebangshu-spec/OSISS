import assert from 'node:assert/strict'
import { test } from 'node:test'

import { adminTokenFromEnv, checkProfileUrl, isAllowedApiPath } from './profiles.ts'

test('https anywhere, http only on loopback or with explicit opt-in', () => {
  assert.deepEqual(checkProfileUrl('https://search.college.edu/', 'remote'), { ok: true, url: 'https://search.college.edu' })
  assert.deepEqual(checkProfileUrl('https://college.edu/osiss/', 'remote'), { ok: true, url: 'https://college.edu/osiss' })
  assert.deepEqual(checkProfileUrl('http://localhost:8000', 'local'), { ok: true, url: 'http://localhost:8000' })
  assert.equal(checkProfileUrl('http://127.0.0.1:8000', 'remote').ok, true)
  assert.equal(checkProfileUrl('http://192.168.1.10:8000', 'remote').ok, false)
  assert.equal(checkProfileUrl('http://192.168.1.10:8000', 'remote', true).ok, true)
})

test('local profiles must point at this machine', () => {
  assert.equal(checkProfileUrl('https://search.college.edu', 'local').ok, false)
  assert.equal(checkProfileUrl('http://[::1]:8000', 'local').ok, true)
})

test('malformed or unsafe URLs are refused', () => {
  for (const url of ['search.college.edu', 'ftp://x.edu', 'https://user:pw@x.edu', 'https://x.edu/?a=1', 'file:///etc']) {
    assert.equal(checkProfileUrl(url, 'remote').ok, false, url)
  }
})

test('ADMIN_TOKEN is read from .env text', () => {
  assert.equal(adminTokenFromEnv('A=1\nADMIN_TOKEN=abc123\n'), 'abc123')
  assert.equal(adminTokenFromEnv('export ADMIN_TOKEN="quoted value"'), 'quoted value')
  assert.equal(adminTokenFromEnv('# ADMIN_TOKEN=commented\nOTHER=1'), null)
  assert.equal(adminTokenFromEnv('ADMIN_TOKEN='), null)
})

test('the renderer may only reach admin routes', () => {
  assert.ok(isAllowedApiPath('/api/admin/books'))
  assert.ok(isAllowedApiPath('/health'))
  assert.ok(!isAllowedApiPath('/api/search'))
  assert.ok(!isAllowedApiPath('/api/admin/../search'))
  assert.ok(!isAllowedApiPath('//evil.example/api/admin/x'))
})

// Run with: npm test  (node --experimental-strip-types --test)
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import {
  CODE_ALPHABET,
  CODE_LENGTH,
  FREE_ATTEMPTS,
  Vault,
  generateCode,
  lockDuration,
  normalizeCode,
  type OsSealer,
  type StoredProfile,
} from './vault.ts'

const profile: StoredProfile = {
  id: 'p1',
  name: 'Localhost',
  url: 'http://localhost:8000',
  kind: 'local',
  token: 'secret-admin-token-1234567890',
}

// Stands in for Electron safeStorage: reversible, but bound to this "machine" via a fixed XOR key.
const fakeSealer = (available = true): OsSealer => ({
  isAvailable: () => available,
  encrypt: (plain) => Buffer.from(Buffer.from(plain).map((byte) => byte ^ 0x5a)),
  decrypt: (sealed) => Buffer.from(sealed.map((byte) => byte ^ 0x5a)).toString(),
})

function newVault(sealer: OsSealer | null = fakeSealer(), now = () => Date.now()) {
  return new Vault(mkdtempSync(join(tmpdir(), 'osiss-vault-')), sealer, now)
}

async function setUp(vault: Vault) {
  const code = vault.beginSetup()
  assert.equal(await vault.confirmSetup(code.toLowerCase().slice(0, 3) + '-' + code.slice(3)), true)
  return code
}

test('generated codes use the unambiguous alphabet', () => {
  for (let i = 0; i < 200; i += 1) {
    const code = generateCode()
    assert.equal(code.length, CODE_LENGTH)
    assert.ok([...code].every((char) => CODE_ALPHABET.includes(char)))
  }
  assert.ok(!/[01OIL]/.test(CODE_ALPHABET))
  assert.equal(normalizeCode(' k7m-q3x '), 'K7MQ3X')
})

test('setup needs the shown code typed back', async () => {
  const vault = newVault()
  vault.beginSetup()
  assert.equal(await vault.confirmSetup('WRONG1'), false)
  assert.equal(vault.status().initialized, false)
})

test('profiles are encrypted at rest and readable only with the passkey', async () => {
  const vault = newVault()
  const code = await setUp(vault)
  vault.saveProfiles([profile])
  const onDisk = readFileSync(join((vault as unknown as { dir: string }).dir, 'vault.json'), 'utf8')
  assert.ok(!onDisk.includes(profile.token) && !onDisk.includes('localhost'))
  assert.equal(vault.status().osProtected, true)

  vault.lock()
  assert.throws(() => vault.profiles())
  assert.equal(await vault.unlock('ZZZZZZ'), 'Wrong passkey.')
  assert.equal(await vault.unlock(code), null)
  assert.deepEqual(vault.profiles(), [profile])
})

test('a vault copied to a machine without the keychain cannot be opened', async () => {
  const vault = newVault()
  const code = await setUp(vault)
  vault.saveProfiles([profile])
  const dir = (vault as unknown as { dir: string }).dir
  const elsewhere = new Vault(dir, fakeSealer(false))
  assert.match((await elsewhere.unlock(code)) ?? '', /keychain/)
})

test('wrong passkeys lock the vault for a growing time', async () => {
  let now = 1_000_000
  const vault = newVault(null, () => now)
  const code = await setUp(vault)
  vault.lock()
  for (let i = 0; i < FREE_ATTEMPTS; i += 1) assert.equal(await vault.unlock('AAAAAA'), 'Wrong passkey.')
  const status = vault.status()
  assert.equal(status.failures, FREE_ATTEMPTS)
  assert.equal(status.lockedUntil, now + lockDuration(FREE_ATTEMPTS))

  // Even the right code is refused while locked.
  assert.match((await vault.unlock(code)) ?? '', /Too many/)
  now = status.lockedUntil + 1
  assert.equal(await vault.unlock('AAAAAA'), 'Wrong passkey.')
  assert.equal(vault.status().lockedUntil, now + lockDuration(FREE_ATTEMPTS + 1))
  assert.ok(lockDuration(FREE_ATTEMPTS + 1) > lockDuration(FREE_ATTEMPTS))
  assert.equal(lockDuration(50), 15 * 60_000)

  now = vault.status().lockedUntil + 1
  assert.equal(await vault.unlock(code), null)
  assert.equal(vault.status().failures, 0)
})

test('changing to a passphrase keeps the profiles', async () => {
  const vault = newVault()
  const code = await setUp(vault)
  vault.saveProfiles([profile])
  assert.match((await vault.changePasskey(code, 'short', 'passphrase')) ?? '', /at least/)
  assert.equal(await vault.changePasskey(code, 'correct horse battery', 'passphrase'), null)
  vault.lock()
  assert.equal(vault.status().passkeyKind, 'passphrase')
  assert.equal(await vault.unlock(code), 'Wrong passkey.')
  assert.equal(await vault.unlock('correct horse battery'), null)
  assert.deepEqual(vault.profiles(), [profile])
})

test('reset wipes everything', async () => {
  const vault = newVault()
  await setUp(vault)
  vault.saveProfiles([profile])
  vault.reset()
  assert.equal(vault.status().initialized, false)
  assert.equal(vault.isUnlocked, false)
})

import assert from 'node:assert/strict'
import test from 'node:test'

import { INSTALL_SCRIPT_URL, installFlags, serverInstallCommand, serverInstallFileCommand, validateInstallDir } from '../shared/install.ts'

test('the one-line command', () => {
  assert.equal(serverInstallCommand(), `curl -fsSL ${INSTALL_SCRIPT_URL} | bash`)
  assert.equal(serverInstallCommand({ listenAll: true }), `curl -fsSL ${INSTALL_SCRIPT_URL} | bash -s -- --listen all`)
  assert.equal(
    serverInstallCommand({ listenAll: true, service: true, noWebsite: true }),
    `curl -fsSL ${INSTALL_SCRIPT_URL} | bash -s -- --listen all --service --no-website`,
  )
})

test('the command for a saved copy of the script', () => {
  assert.equal(serverInstallFileCommand(), 'bash install.sh')
  assert.equal(serverInstallFileCommand({ listenAll: true, service: true }), 'bash install.sh --listen all --service')
  assert.deepEqual(installFlags({}), [])
})

test('the installer is fetched over https from the project site', () => {
  assert.ok(INSTALL_SCRIPT_URL.startsWith('https://'))
  assert.ok(INSTALL_SCRIPT_URL.endsWith('/install.sh'))
})

test('install folders', () => {
  assert.equal(validateInstallDir('/home/alice/osiss'), null)
  assert.equal(validateInstallDir('/opt/osiss'), null)
  assert.equal(validateInstallDir('/home/alice/my server'), null)
  for (const bad of ['', '  ', 'osiss', './osiss', '/', '/usr', '/usr/local/osiss', '/etc/', '/root', '/var']) {
    assert.notEqual(validateInstallDir(bad), null, JSON.stringify(bad))
  }
})

import assert from 'node:assert/strict'
import test from 'node:test'

import { buildCaddyfile, domainError, ipError, publicSteps } from '../src/lib/caddy.ts'

test('domainError accepts real domains and explains the rest', () => {
  for (const good of ['search.example.edu', 'ossis.com', 'Library.College.AC.IN', 'a-b.example.org']) assert.equal(domainError(good), null, good)
  assert.match(domainError('')!, /Enter your domain/)
  assert.match(domainError('https://ossis.com')!, /without https/)
  assert.match(domainError('ossis.com/search')!, /without https/)
  for (const bad of ['localhost', 'ossis', '-bad.com', 'bad-.com', 'two words.com', 'x.c', 'ex ample.com']) assert.match(domainError(bad)!, /not a valid domain/, bad)
})

test('ipError accepts IPv4 and IPv6 or nothing', () => {
  for (const good of ['', '  ', '203.0.113.10', '10.0.0.1', '2001:db8::1']) assert.equal(ipError(good), null, good)
  for (const bad of ['256.1.1.1', '1.2.3', 'example.com', '1.2.3.4.5']) assert.match(ipError(bad)!, /not an IP address/, bad)
})

test('buildCaddyfile restricts the admin API only when an address is given', () => {
  assert.equal(
    buildCaddyfile(' Search.Example.EDU ', '203.0.113.10'),
    'search.example.edu {\n\tencode gzip\n\n\t@admin_from_elsewhere {\n\t\tpath /api/admin/*\n\t\tnot remote_ip 203.0.113.10\n\t}\n\trespond @admin_from_elsewhere "Forbidden" 403\n\n\treverse_proxy 127.0.0.1:8000\n}\n',
  )
  assert.equal(buildCaddyfile('ossis.com', '', 8001), 'ossis.com {\n\tencode gzip\n\n\treverse_proxy 127.0.0.1:8001\n}\n')
})

test('publicSteps names the domain and the commands', () => {
  const steps = publicSteps('Ossis.com')
  assert.ok(steps[0].includes('A record for ossis.com'))
  assert.ok(steps.includes('sudo systemctl reload caddy'))
  assert.ok(steps.some((line) => line.includes('https://ossis.com')))
})

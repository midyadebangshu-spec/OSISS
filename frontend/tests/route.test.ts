import assert from 'node:assert/strict'
import test from 'node:test'
import { isUnknownPath, parseHash } from '../src/lib/route.ts'

test('known routes are unchanged', () => {
    assert.deepEqual(parseHash(''), { page: 'search' })
    assert.deepEqual(parseHash('#'), { page: 'search' })
    assert.deepEqual(parseHash('#/'), { page: 'search' })
    assert.deepEqual(parseHash('#/explore/12'), { page: 'explore', conceptId: 12 })
    assert.deepEqual(parseHash('#/library/3/read/9'), { page: 'library', bookId: 3, readPage: 9 })
    assert.deepEqual(parseHash('#/account'), { page: 'account' })
    assert.deepEqual(parseHash('#/workspace/notes/4'), { page: 'workspace', tab: 'notes', id: 4 })
    assert.deepEqual(parseHash('#/workspace'), { page: 'workspace', tab: 'dashboard', id: null })
})

test('an unrecognised hash route is not found, and keeps the address for display', () => {
    assert.deepEqual(parseHash('#/nope'), { page: 'notfound', address: '#/nope' })
    assert.deepEqual(parseHash('#/workspace/bogus'), { page: 'notfound', address: '#/workspace/bogus' })
    assert.deepEqual(parseHash('#/explore/abc'), { page: 'notfound', address: '#/explore/abc' })
})

test('only "/" and "/index.html" are known paths', () => {
    assert.equal(isUnknownPath('/'), false)
    assert.equal(isUnknownPath('/index.html'), false)
    assert.equal(isUnknownPath('/foo'), true)
    assert.equal(isUnknownPath('/library/3'), true)
    assert.deepEqual(parseHash('#/', '/foo'), { page: 'notfound', address: '/foo#/' })
    assert.deepEqual(parseHash('', '/foo/bar'), { page: 'notfound', address: '/foo/bar' })
})

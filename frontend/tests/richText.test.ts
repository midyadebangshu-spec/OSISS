import assert from 'node:assert/strict'
import test from 'node:test'
import { citationSnippet, insertAt, italicRuns } from '../src/lib/richText.ts'

test('italicRuns splits on *markers*', () => {
    assert.deepEqual(italicRuns('Smith (2000). *A Title*. Press.'), [
        { text: 'Smith (2000). ', italic: false },
        { text: 'A Title', italic: true },
        { text: '. Press.', italic: false },
    ])
    assert.deepEqual(italicRuns('plain'), [{ text: 'plain', italic: false }])
    assert.deepEqual(italicRuns(''), [])
})

test('citationSnippet builds the three kinds of insertion', () => {
    assert.equal(citationSnippet('block', 12, 'A  stack\nis LIFO.'), '\n\n> A stack is LIFO. [@e12]\n\n')
    assert.equal(citationSnippet('inline', 3, 'x y'), '“x y” [@e3]')
    assert.equal(citationSnippet('cite', 7, 'ignored'), ' [@e7]')
})

test('insertAt replaces the selection and reports the cursor', () => {
    assert.deepEqual(insertAt('abcdef', 2, 4, 'XY!'), { text: 'abXY!ef', cursor: 5 })
    assert.deepEqual(insertAt('abc', 3, 3, '_'), { text: 'abc_', cursor: 4 })
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { insideRect, lineRects, orderedRange, selectedText, wordIndexAt } from '../src/lib/selection.ts'
import type { ReaderWord } from '../src/types/library.ts'

// Two lines of text: "A stack is" and "last in, first-" / "out".
const words: ReaderWord[] = [
    [0.1, 0.1, 0.15, 0.12, 'A', 0],
    [0.17, 0.1, 0.3, 0.12, 'stack', 0],
    [0.32, 0.1, 0.4, 0.12, 'is', 0],
    [0.1, 0.13, 0.2, 0.15, 'last-', 1],
    [0.1, 0.16, 0.2, 0.18, 'in', 2],
]

test('wordIndexAt finds the word under the point, else the nearest', () => {
    assert.equal(wordIndexAt(words, 0.2, 0.11), 1)
    assert.equal(wordIndexAt(words, 0.9, 0.11), 2) // right of the line: its last word
    assert.equal(wordIndexAt(words, 0.12, 0.5), 4) // below everything: the last line
    assert.equal(wordIndexAt([], 0.5, 0.5), -1)
})

test('orderedRange orders a drag in either direction', () => {
    assert.deepEqual(orderedRange(3, 1), [1, 3])
    assert.deepEqual(orderedRange(1, 3), [1, 3])
})

test('lineRects gives one rectangle per line', () => {
    const rects = lineRects(words, 0, 3)
    assert.equal(rects.length, 2)
    assert.deepEqual(rects[0], [0.1, 0.1, 0.4, 0.12])
    assert.deepEqual(rects[1], [0.1, 0.13, 0.2, 0.15])
    assert.deepEqual(lineRects(words, 1, 1), [[0.17, 0.1, 0.3, 0.12]])
    assert.deepEqual(lineRects(words, 0, 99).length, 3) // clipped to the page's words
})

test('selectedText joins words and rejoins a hyphenated line break', () => {
    assert.equal(selectedText(words, 0, 2), 'A stack is')
    assert.equal(selectedText(words, 2, 4), 'is lastin')
    assert.equal(selectedText(words, 1, 1), 'stack')
})

test('insideRect', () => {
    assert.ok(insideRect([0.1, 0.1, 0.4, 0.12], 0.2, 0.11))
    assert.ok(!insideRect([0.1, 0.1, 0.4, 0.12], 0.5, 0.11))
})

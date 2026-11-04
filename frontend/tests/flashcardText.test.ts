import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
    cardFromDefinition,
    cardFromResult,
    cardText,
    clampLines,
    promptFromQuery,
    questionFor,
    scriptOf,
    slugify,
    toRuns,
    truncateAtSentence,
    wrapRuns,
} from '../src/lib/flashcardText.ts'
import type { SearchResult } from '../src/types/search.ts'

// One "pixel" per character, bold one wider: enough to test wrapping without a canvas.
const measure = (text: string, bold: boolean) => text.length * (bold ? 1.5 : 1)

function result(overrides: Partial<SearchResult> = {}): SearchResult {
    return {
        exact_quote: 'Osmosis is the movement of water through a semipermeable membrane.',
        paragraph_text: 'Context. Osmosis is the movement of water through a semipermeable membrane. More.',
        answer_text: 'movement of water',
        book_title: 'Concepts of Biology',
        author: 'OpenStax',
        page_number: 93,
        pdf_link: '/data/pdfs/bio.pdf',
        kind: 'text',
        blocks: [],
        figures: [],
        tables: [],
        formulas: [],
        page: null,
        concepts: [],
        ...overrides,
    }
}

test('questions in the language of the term', () => {
    assert.equal(questionFor('Osmosis'), 'What is osmosis?')
    assert.equal(questionFor('DNA'), 'What is DNA?')
    assert.equal(questionFor('আলোর প্রতিফলন'), 'আলোর প্রতিফলন কাকে বলে?')
    assert.equal(questionFor('संक्षारण'), 'संक्षारण क्या है?')
    assert.equal(scriptOf('ऊष्मा and heat'), 'hi')
})

test('the searched question becomes the prompt when there is no definition', () => {
    assert.equal(promptFromQuery('  difference between stack and queue '), 'Difference between stack and queue?')
    assert.equal(promptFromQuery('What is osmosis?'), 'What is osmosis?')
    const card = cardFromResult(result(), 'what is osmosis')
    assert.equal(card.question, 'What is osmosis?')
    assert.equal(card.highlight, 'movement of water')
    assert.equal(card.source, 'Concepts of Biology · p. 93')
    assert.equal(card.slug, 'what-is-osmosis')
})

test('a definition result asks for the term and carries list and figure', () => {
    const card = cardFromResult(
        result({
            blocks: [
                { type: 'definition', term: 'Osmosis', text: 'Osmosis is the movement of water.' },
                { type: 'list', ordered: true, markers: ['(i)', '(ii)'], items: ['water moves', 'solute stays'] },
            ],
            figures: [{ image: '/data/assets/x/p1-f1.webp', caption: 'Figure 3.21 Osmosis', page_number: 93, primary: true }],
        }),
        'tell me about it',
    )
    assert.equal(card.question, 'What is osmosis?')
    assert.equal(card.highlight, 'Osmosis')
    assert.deepEqual(card.list, { ordered: true, markers: ['(i)', '(ii)'], items: ['water moves', 'solute stays'] })
    assert.equal(card.figure?.caption, 'Figure 3.21 Osmosis')
    assert.equal(card.slug, 'osmosis')
    assert.match(cardText(card), /\(i\) water moves\n\(ii\) solute stays/)
})

test('Bengali and Hindi cards and slugs', () => {
    const bn = cardFromDefinition('আলোর প্রতিফলন', 'মসৃণ তলে আলোর প্রতিফলন ঘটে।', 'Science, Class 7', 38)
    assert.equal(bn.question, 'আলোর প্রতিফলন কাকে বলে?')
    assert.equal(bn.language, 'bn')
    assert.equal(bn.slug, 'card')  // no Latin letters to name the file with
    assert.equal(slugify('Binary Search Tree!'), 'binary-search-tree')
})

test('long answers are cut at a sentence end', () => {
    const text = 'The first sentence is long enough to pass the halfway mark. ' + 'word '.repeat(200)
    assert.equal(truncateAtSentence(text, 100), 'The first sentence is long enough to pass the halfway mark.')
    // A sentence end before the halfway mark would throw too much away: cut at a word instead.
    assert.ok(truncateAtSentence('Short one. ' + 'word '.repeat(200), 100).endsWith('…'))
    assert.ok(truncateAtSentence('word '.repeat(50), 60).endsWith('…'))
    assert.equal(truncateAtSentence('short', 60), 'short')
})

test('runs, wrapping and clamping', () => {
    assert.deepEqual(toRuns('Osmosis moves water; osmosis matters', 'osmosis'), [
        { text: 'Osmosis', bold: true },
        { text: ' moves water; ', bold: false },
        { text: 'osmosis', bold: true },
        { text: ' matters', bold: false },
    ])
    const lines = wrapRuns(toRuns('aaa bbb ccc ddd', 'bbb'), 9, measure)
    assert.deepEqual(lines.map((line) => line.map((run) => run.text).join('')), ['aaa bbb', 'ccc ddd'])
    assert.ok(lines[0].some((run) => run.bold && run.text === 'bbb'))
    const long = wrapRuns([{ text: 'abcdefghijkl', bold: false }], 5, measure)
    assert.deepEqual(long.map((line) => line[0].text), ['abcde', 'fghij', 'kl'])
    const clamped = clampLines(wrapRuns([{ text: 'one two three four five six', bold: false }], 8, measure), 2)
    assert.equal(clamped.length, 2)
    assert.ok(clamped[1][clamped[1].length - 1].text.endsWith('…'))
})

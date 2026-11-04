// What goes on a flashcard and how its text is laid out. Pure functions (no DOM), so they run under Node's test
// runner (frontend/tests); the drawing is in flashcardRender.ts. Everything on a card comes from the book.

import type { SearchResult } from '../types/search.ts'
import { cleanBookText } from './textFormat.ts'

export type CardLanguage = 'en' | 'bn' | 'hi'

export type CardList = { ordered: boolean; markers?: string[] | null; items: string[] }

export type CardContent = {
    question: string
    answer: string
    /** Phrase set in bold on the back (the exact answer, or the defined term). */
    highlight?: string
    list?: CardList
    figure?: { image: string; caption?: string | null }
    /** "Book title · p. 93" */
    source: string
    language: CardLanguage
    /** File-name stem: "osmosis" -> flashcard-osmosis.png */
    slug: string
}

/** A styled piece of text; lines are arrays of runs. */
export type Run = { text: string; bold: boolean }

const MAX_ANSWER_CHARS = 460
const MAX_LIST_ITEMS = 6

export function scriptOf(text: string): CardLanguage {
    let bengali = 0
    let devanagari = 0
    let latin = 0
    for (const char of text) {
        const code = char.codePointAt(0) ?? 0
        if (code >= 0x0980 && code <= 0x09ff) bengali++
        else if (code >= 0x0900 && code <= 0x097f) devanagari++
        else if (/[A-Za-z]/.test(char)) latin++
    }
    // Indic text wins once it is a sizeable share: it decides which fonts the card waits for.
    if (bengali + devanagari === 0 || (bengali + devanagari) * 3 < latin) return 'en'
    return bengali >= devanagari ? 'bn' : 'hi'
}

/** "What is X?" in the language of the term. */
export function questionFor(term: string, language: CardLanguage = scriptOf(term)): string {
    const clean = term.trim()
    if (language === 'bn') return `${clean} কাকে বলে?`
    if (language === 'hi') return `${clean} क्या है?`
    return `What is ${/^[A-Z]{2,}/.test(clean) ? clean : clean.charAt(0).toLowerCase() + clean.slice(1)}?`
}

/** The searched question as a card prompt: capitalised, with a question mark when it reads as a question. */
export function promptFromQuery(query: string): string {
    const text = query.trim().replace(/\s+/g, ' ')
    if (!text) return ''
    const capitalised = scriptOf(text) === 'en' ? text.charAt(0).toUpperCase() + text.slice(1) : text
    return /[?？।!.]$/.test(capitalised) ? capitalised : `${capitalised}?`
}

/** Cut long text at a sentence end (or a word end) before `max` characters. */
export function truncateAtSentence(text: string, max = MAX_ANSWER_CHARS): string {
    const clean = text.replace(/\s+/g, ' ').trim()
    if (clean.length <= max) return clean
    const head = clean.slice(0, max)
    const sentenceEnd = Math.max(head.lastIndexOf('. '), head.lastIndexOf('। '), head.lastIndexOf('? '), head.lastIndexOf('! '))
    if (sentenceEnd > max * 0.5) return head.slice(0, sentenceEnd + 1)
    const wordEnd = head.lastIndexOf(' ')
    return `${head.slice(0, wordEnd > 0 ? wordEnd : max)}…`
}

export function slugify(text: string): string {
    const ascii = text
        .normalize('NFKD')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 48)
        .replace(/-+$/, '')
    return ascii || 'card'
}

function bookText(text: string): string {
    return cleanBookText(text).replace(/\n?•\s*/g, ' ').replace(/\s+/g, ' ').trim()
}

/** Card for a search hit: a definition becomes "What is X?", otherwise the searched question is the prompt. */
export function cardFromResult(result: SearchResult, query: string): CardContent {
    const definition = result.blocks.find((block) => block.type === 'definition')
    const listBlock = result.blocks.find((block) => block.type === 'list')
    const figure = result.figures.find((item) => item.primary && item.caption) ?? result.figures.find((item) => item.primary)
    const source = `${result.book_title} · p. ${result.page_number}`

    let question: string
    let answer: string
    let highlight: string | undefined
    let slugSource: string
    if (definition && definition.type === 'definition') {
        const language = scriptOf(definition.term)
        question = questionFor(definition.term, language)
        answer = truncateAtSentence(bookText(definition.text))
        highlight = definition.term
        slugSource = definition.term
    } else {
        question = promptFromQuery(query) || result.book_title
        answer = truncateAtSentence(bookText(result.exact_quote || result.paragraph_text))
        highlight = result.answer_text ? bookText(result.answer_text) : undefined
        slugSource = query || result.book_title
    }

    const card: CardContent = { question, answer, highlight, source, language: scriptOf(`${question} ${answer}`), slug: slugify(slugSource) }
    if (listBlock && listBlock.type === 'list' && listBlock.items.length) {
        card.list = {
            ordered: listBlock.ordered,
            markers: listBlock.markers?.slice(0, MAX_LIST_ITEMS) ?? null,
            items: listBlock.items.slice(0, MAX_LIST_ITEMS).map((item) => truncateAtSentence(bookText(item), 140)),
        }
    }
    if (figure) card.figure = { image: figure.image, caption: figure.caption }
    return card
}

/** Card for a definition sentence shown elsewhere (the Explorer's "How the books define it"). */
export function cardFromDefinition(term: string, sentence: string, bookTitle: string, page: number): CardContent {
    const language = scriptOf(term)
    return {
        question: questionFor(term, language),
        answer: truncateAtSentence(bookText(sentence)),
        highlight: term,
        source: `${bookTitle} · p. ${page}`,
        language: scriptOf(`${term} ${sentence}`),
        slug: slugify(term),
    }
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Split text into plain / bold runs around every (case-insensitive) occurrence of `highlight`. */
export function toRuns(text: string, highlight?: string): Run[] {
    const phrase = highlight?.trim()
    if (!phrase || phrase.length < 2) return [{ text, bold: false }]
    const parts = text.split(new RegExp(`(${escapeRegExp(phrase)})`, 'iu'))
    return parts.filter((part) => part).map((part) => ({ text: part, bold: part.toLowerCase() === phrase.toLowerCase() }))
}

/**
 * Greedy word wrap of styled runs into lines no wider than `maxWidth`, using `measure` (canvas measureText in the
 * browser, a stub in tests). Words longer than a line are broken by characters.
 */
export function wrapRuns(runs: Run[], maxWidth: number, measure: (text: string, bold: boolean) => number): Run[][] {
    const words: Run[] = []
    for (const run of runs) {
        for (const piece of run.text.split(/(\s+)/)) {
            if (piece) words.push({ text: piece, bold: run.bold })
        }
    }
    const lines: Run[][] = [[]]
    let width = 0
    const push = (word: Run) => {
        const line = lines[lines.length - 1]
        const last = line[line.length - 1]
        if (last && last.bold === word.bold) last.text += word.text
        else line.push({ ...word })
    }
    for (const word of words) {
        const isSpace = /^\s+$/.test(word.text)
        const wordWidth = measure(word.text, word.bold)
        if (isSpace) {
            if (width > 0) {
                push({ text: ' ', bold: word.bold })
                width += measure(' ', word.bold)
            }
            continue
        }
        if (width + wordWidth > maxWidth && width > 0) {
            const line = lines[lines.length - 1]
            const last = line[line.length - 1]
            if (last) last.text = last.text.replace(/\s+$/, '')
            lines.push([])
            width = 0
        }
        if (wordWidth > maxWidth) {
            let chunk = ''
            for (const char of word.text) {
                if (measure(chunk + char, word.bold) > maxWidth && chunk) {
                    push({ text: chunk, bold: word.bold })
                    lines.push([])
                    chunk = ''
                }
                chunk += char
            }
            push({ text: chunk, bold: word.bold })
            width = measure(chunk, word.bold)
            continue
        }
        push(word)
        width += wordWidth
    }
    return lines.filter((line) => line.length > 0)
}

/** Keep at most `maxLines` lines, ending the last kept one with an ellipsis. */
export function clampLines(lines: Run[][], maxLines: number): Run[][] {
    if (lines.length <= maxLines) return lines
    const kept = lines.slice(0, maxLines).map((line) => line.map((run) => ({ ...run })))
    const last = kept[kept.length - 1]
    const tail = last[last.length - 1]
    tail.text = `${tail.text.replace(/[\s.,;:]+$/, '')}…`
    return kept
}

/** Plain-text version of a card (for "Copy text"). */
export function cardText(card: CardContent): string {
    const list = card.list
        ? `\n${card.list.items.map((item, index) => `${card.list?.ordered ? `${card.list.markers?.[index] ?? `${index + 1}.`}` : '•'} ${item}`).join('\n')}`
        : ''
    return `${card.question}\n\n${card.answer}${list}\n\n— ${card.source}`
}

// Display helpers for book text. The text comes from PDF extraction/OCR, so it has stray control
// characters, spaces before punctuation, bullet glyphs and loose page numbers.

export type Segment = {
    text: string
    kind: 'context' | 'quote' | 'answer'
}

// Control characters, zero-width spaces/BOM and private-use glyphs that PDF extraction leaves behind.
// Zero-width joiner/non-joiner are kept: Bengali and Hindi conjuncts need them.
const INVISIBLE = /[\p{Cc}\p{Co}\u200B\u2060\uFEFF]/gu
// Bullets: unambiguous glyphs anywhere, or "*" / "©" (OCR for a bullet) only before Bengali/Hindi text,
// because "*" also means pointer/multiplication in the programming books.
const BULLET = /\s*(?:[∑•●▪◦■]|(?<=\s)[*©](?=\s+[\u0900-\u0DFF]))\s+/g

// A quote can start with the bullet glyph of its list item; the paragraph keeps that glyph outside the match.
const LEADING_BULLET = /^\s*(?:[∑•●▪◦■*©]\s+)+/

export function cleanBookText(raw: string): string {
    return raw
        .replace(/\s+/g, ' ')
        .replace(INVISIBLE, '')
        .replace(/ +([,.;:!?।)\]])/g, '$1')
        .replace(BULLET, '\n• ')
        .trim()
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Find `needle` in `haystack`, ignoring differences in whitespace and case.
function findLoose(haystack: string, needle: string): { start: number; end: number } | null {
    const words = needle.trim().split(/\s+/).filter(Boolean)
    if (words.length === 0) return null
    const match = new RegExp(words.map(escapeRegExp).join('\\s+'), 'iu').exec(haystack)
    return match ? { start: match.index, end: match.index + match[0].length } : null
}

// Drop a lone page-number-like token at the outer edge of the excerpt (never inside the quote).
function trimEdges(before: string, after: string): { before: string; after: string } {
    return {
        before: before.replace(/^\s*\d{1,4}(?:\s+(?=\D)|\s*$)/, ''),
        after: after.replace(/\s+\d{1,4}\s*$/, ''),
    }
}

/**
 * Split the matched paragraph into context / quote / answer segments so the UI can highlight the
 * quoted sentence and, inside it, the exact answer. Falls back to plain text when the quote cannot
 * be located.
 */
export function buildParagraphSegments(paragraph: string, quote: string, answer?: string): Segment[] {
    const text = cleanBookText(paragraph)
    const cleanQuote = cleanBookText(quote).replace(LEADING_BULLET, '')
    const quoteSpan = findLoose(text, cleanQuote)

    if (!quoteSpan) {
        return text ? [{ text, kind: 'context' }] : []
    }

    const edges = trimEdges(text.slice(0, quoteSpan.start), text.slice(quoteSpan.end))
    const quoteText = text.slice(quoteSpan.start, quoteSpan.end)
    return [
        { text: edges.before, kind: 'context' as const },
        ...splitAnswer(quoteText, answer),
        { text: edges.after, kind: 'context' as const },
    ].filter((segment) => segment.text.length > 0)
}

/** Split a quote into quote / answer / quote segments around the exact answer phrase. */
function splitAnswer(quote: string, answer?: string): Segment[] {
    const cleanAnswer = answer ? cleanBookText(answer).replace(/^[\s.,;:!?।]+|[\s.,;:!?।]+$/g, '') : ''
    const span = cleanAnswer ? findLoose(quote, cleanAnswer) : null
    if (!span) return [{ text: quote, kind: 'quote' }]
    return [
        { text: quote.slice(0, span.start), kind: 'quote' as const },
        { text: quote.slice(span.start, span.end), kind: 'answer' as const },
        { text: quote.slice(span.end), kind: 'quote' as const },
    ].filter((segment) => segment.text.length > 0)
}

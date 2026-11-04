import type { ReaderWord } from '../types/library.ts'

/** [x0, y0, x1, y1] as fractions of the page (0..1). */
export type Rect = [number, number, number, number]

/**
 * Index of the word under the point (x, y given as page fractions), else the nearest word. `aspect` is page height
 * divided by width, so distances are measured in the same units on both axes. -1 when the page has no words.
 */
export function wordIndexAt(words: ReaderWord[], x: number, y: number, aspect = 1): number {
    let best = -1
    let bestDistance = Infinity
    for (let index = 0; index < words.length; index += 1) {
        const [x0, y0, x1, y1] = words[index]
        const dx = Math.max(x0 - x, 0, x - x1)
        const dy = Math.max(y0 - y, 0, y - y1) * aspect
        const distance = Math.hypot(dx, dy)
        if (distance < bestDistance) {
            best = index
            bestDistance = distance
            if (distance === 0) break
        }
    }
    return best
}

/** The two ends of a drag as an ordered inclusive range. */
export function orderedRange(a: number, b: number): [number, number] {
    return a <= b ? [a, b] : [b, a]
}

/** The selected words as one rectangle per text line (so a highlight follows the lines, not a block). */
export function lineRects(words: ReaderWord[], from: number, to: number): Rect[] {
    const rects: Rect[] = []
    let line: number | null = null
    for (let index = from; index <= to && index < words.length; index += 1) {
        const [x0, y0, x1, y1, , lineId] = words[index]
        if (line === lineId && rects.length > 0) {
            const last = rects[rects.length - 1]
            last[0] = Math.min(last[0], x0)
            last[1] = Math.min(last[1], y0)
            last[2] = Math.max(last[2], x1)
            last[3] = Math.max(last[3], y1)
        } else {
            rects.push([x0, y0, x1, y1])
            line = lineId
        }
    }
    return rects.map(([a, b, c, d]) => [clamp(a), clamp(b), clamp(c), clamp(d)] as Rect)
}

/** The selected words as text: words joined by spaces, lines by a space (a hyphen at a line end joins the halves). */
export function selectedText(words: ReaderWord[], from: number, to: number): string {
    let text = ''
    let line: number | null = null
    for (let index = from; index <= to && index < words.length; index += 1) {
        const [, , , , word, lineId] = words[index]
        if (text && line !== lineId && text.endsWith('-') && !text.endsWith(' -')) {
            text = text.slice(0, -1) + word
        } else {
            text += (text ? ' ' : '') + word
        }
        line = lineId
    }
    return text
}

function clamp(value: number): number {
    return Math.min(1, Math.max(0, Math.round(value * 10000) / 10000))
}

/** Whether a page-fraction point lies inside a rectangle (used to pick a highlight by clicking it). */
export function insideRect(rect: Rect, x: number, y: number): boolean {
    return x >= rect[0] && x <= rect[2] && y >= rect[1] && y <= rect[3]
}

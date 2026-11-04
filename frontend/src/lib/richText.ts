/** Split text with `*italic*` markers (as the server renders drafts and citations) into runs. */
export type Run = { text: string; italic: boolean }

export function italicRuns(text: string): Run[] {
    const runs: Run[] = []
    const pattern = /\*([^*]+)\*/g
    let position = 0
    for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
        if (match.index > position) runs.push({ text: text.slice(position, match.index), italic: false })
        runs.push({ text: match[1], italic: true })
        position = match.index + match[0].length
    }
    if (position < text.length) runs.push({ text: text.slice(position), italic: false })
    return runs
}

/** Text to put at the cursor for a saved item, as a block quotation, an inline quotation or a bare citation. */
export function citationSnippet(kind: 'block' | 'inline' | 'cite', evidenceId: number, quote: string): string {
    const text = quote.replace(/\s+/g, ' ').trim()
    if (kind === 'block') return `\n\n> ${text} [@e${evidenceId}]\n\n`
    if (kind === 'inline') return `“${text}” [@e${evidenceId}]`
    return ` [@e${evidenceId}]`
}

/** `text` with `insert` placed over the selection [start, end), and where the cursor ends up. */
export function insertAt(text: string, start: number, end: number, insert: string): { text: string; cursor: number } {
    return { text: text.slice(0, start) + insert + text.slice(end), cursor: start + insert.length }
}

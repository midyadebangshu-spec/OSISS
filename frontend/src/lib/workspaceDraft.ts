import type { SearchResult } from '../types/search'
import type { EvidenceDraft } from '../types/workspace'

/** The evidence a result card saves: its extractive quote with book and page. Null when the hit has no book id. */
export function evidenceFromResult(result: SearchResult): EvidenceDraft | null {
    const quote = (result.exact_quote || '').trim()
    if (!result.book_id || !quote) return null
    // A definition keeps its term, so a flashcard can ask "What is <term>?" without anyone typing the question.
    const definition = result.blocks.find((block) => block.type === 'definition')
    const term = result.kind === 'definition' && definition && definition.type === 'definition' ? definition.term.trim() : ''
    return {
        kind: result.kind === 'definition' ? 'definition' : 'passage',
        book_id: result.book_id,
        page_number: result.page_number,
        quote,
        ...(term ? { meta: { term } } : {}),
    }
}

export type User = {
    id: number
    email: string
    name: string
    must_change_password: boolean
    created_at: string | null
}

export type EvidenceKind = 'passage' | 'definition' | 'figure' | 'table' | 'formula' | 'page' | 'concept'

export type Evidence = {
    id: number
    kind: EvidenceKind
    book_id: number | null
    book_title: string | null
    book_author: string | null
    page_number: number | null
    quote: string
    concept_key: string | null
    meta: Record<string, unknown>
    created_at: string | null
    collection_ids: number[]
}

/** What the Save button sends; the server fills in the book's title and author itself. */
export type EvidenceDraft = {
    kind: EvidenceKind
    book_id?: number | null
    page_number?: number | null
    quote?: string
    concept_key?: string | null
    meta?: Record<string, unknown>
    collection_id?: number | null
}

export type Collection = {
    id: number
    name: string
    description: string
    item_count: number
    updated_at: string | null
}

export type Note = {
    id: number
    title: string
    body: string
    evidence_id: number | null
    concept_links: string[]
    created_at: string | null
    updated_at: string | null
}

export type Dashboard = {
    counts: { evidence: number; collections: number; notes: number }
    recent_searches: { query: string; at: string }[]
    continue_reading: { book_id: number; title: string; author: string | null; department: string | null; at: string }[]
    recent_evidence: Evidence[]
    collections: Collection[]
    recent_notes: Note[]
    new_books_30_days: number
}

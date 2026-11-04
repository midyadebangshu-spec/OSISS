export type User = {
    id: number
    email: string
    name: string
    must_change_password: boolean
    created_at: string | null
}

export type EvidenceKind = 'passage' | 'definition' | 'figure' | 'table' | 'formula' | 'page' | 'concept' | 'highlight'

export type Evidence = {
    id: number
    kind: EvidenceKind
    book_id: number | null
    book_title: string | null
    book_author: string | null
    page_number: number | null
    /** The number printed on the page when it differs from the PDF position ("233", "xii"); null when unknown. */
    printed_page: string | null
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
    project_id?: number | null
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
    project_id: number | null
    created_at: string | null
    updated_at: string | null
}

export type Dashboard = {
    counts: { evidence: number; collections: number; notes: number }
    recent_searches: { query: string; at: string }[]
    continue_reading: { book_id: number; title: string; author: string | null; department: string | null; at: string; page_number: number | null }[]
    projects: Project[]
    recent_evidence: Evidence[]
    collections: Collection[]
    recent_notes: Note[]
    new_books_30_days: number
}

export type Project = {
    id: number
    title: string
    question: string
    description: string
    book_count: number
    evidence_count: number
    note_count: number
    created_at: string | null
    updated_at: string | null
}

export type ProjectBook = { id: number; title: string; author: string | null; department: string | null; language: string | null }

export type ProjectDetail = Project & { books: ProjectBook[]; evidence: Evidence[]; notes: Note[] }

/** A highlight's rectangles are fractions of the page: [x0, y0, x1, y1] in 0..1. */
export type HighlightRect = [number, number, number, number]

export type CitationStyle = 'apa' | 'mla' | 'chicago' | 'ieee'

export type Draft = {
    id: number
    title: string
    style: CitationStyle
    project_id: number | null
    created_at: string | null
    updated_at: string | null
    body: string
}

export type DraftSummary = Omit<Draft, 'body'> & { excerpt: string }

/** Text carries `*italic*` markers; see lib/richText. */
export type RenderedDraft = {
    title: string
    style: CitationStyle
    blocks: { type: 'heading' | 'paragraph' | 'quote'; level?: number; text: string; line: number }[]
    bibliography: { key: string; text: string }[]
    warnings: { book: string | null; messages: string[] }[]
    missing: number[]
}

export type QuoteStatus = 'verbatim' | 'other_page' | 'partial' | 'not_found' | 'uncited' | 'unknown_source' | 'unchecked'

export type QuoteCheck = {
    kind: 'block' | 'inline'
    text: string
    line: number
    evidence_id: number | null
    status: QuoteStatus
    book?: string | null
    page?: number | null
    printed_page?: string | null
    matched?: number
    closest?: string | null
    found_on_page?: number
    matches_saved_quote?: boolean
}

export type DraftCheck = { quotes: QuoteCheck[]; counts: Record<string, number>; problems: number }

export type ReferenceEntry = { book_id: number; plain: string; marked: string; html: string; warnings: string[] }

export type References = { style: CitationStyle; styles: Record<CitationStyle, string>; entries: ReferenceEntry[] }

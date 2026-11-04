export type DefinitionBlock = { type: 'definition'; term: string; text: string }
export type ListBlock = { type: 'list'; ordered: boolean; title?: string | null; items: string[]; markers?: string[] | null }
export type CodeBlock = { type: 'code'; text: string }
export type ContentBlock = DefinitionBlock | ListBlock | CodeBlock

export type Figure = {
    image: string
    width?: number | null
    height?: number | null
    caption?: string | null
    label?: string | null
    page_number: number
    primary: boolean
}

export type Table = Figure & {
    rows: string[][]
    header?: string[] | null
}

export type Formula = {
    image: string
    text: string
    width?: number | null
    height?: number | null
}

/** Rectangles are fractions of the page: [x0, y0, x1, y1] in 0..1. */
export type Rect = [number, number, number, number]

export type PageSnapshot = {
    image: string
    width: number
    height: number
    scanned: boolean
    highlights: Rect[]
    context: Rect[]
}

export type ResultKind = 'definition' | 'list' | 'code' | 'figure' | 'table' | 'text'

export type SearchResult = {
    exact_quote: string
    paragraph_text: string
    answer_text?: string
    book_title: string
    author: string
    department?: string
    page_number: number
    pdf_link: string
    kind: ResultKind
    blocks: ContentBlock[]
    figures: Figure[]
    tables: Table[]
    formulas: Formula[]
    page?: PageSnapshot | null
    /** Concepts the hit names, for the Context Explorer. */
    concepts: { id: number; label: string; book_count: number }[]
    /** The book's library page (rich results only). */
    book_id?: number | null
}

export type LayoutKind = 'answer' | 'visual' | 'table' | 'compare' | 'steps' | 'empty'

export type Layout = {
    kind: LayoutKind
    hero: number | null
    intents: string[]
}

export type SearchResponse = {
    query: string
    layout: Layout
    results: SearchResult[]
}

/** What the page viewer panel shows. */
export type PagePreview = {
    pdfLink: string
    pageNumber: number
    title: string
}

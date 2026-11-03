import type { Figure } from './search'

export type ConceptSummary = {
    id: number
    label: string
    aliases: string[]
    book_count: number
    mention_count: number
    definition_count: number
}

export type ConceptSearch = { query: string; built: boolean; concepts: ConceptSummary[] }

export type Relation = 'is_a' | 'has_part' | 'used_for' | 'causes' | 'contrast'

export type Neighbour = {
    id: number
    label: string
    aliases: string[]
    book_count: number
    weight: number
    sentence_count: number
    shared_books: number
    relation: Relation | null
    /** "out": the open concept is the relation's source ("stack is a type of data structure"). */
    direction: 'out' | 'in' | null
    /** > 0: most books reach the open concept before this one. */
    order: number
}

export type ConceptBook = { book_id: number; title: string; author?: string | null; pdf_link: string; pages: number[]; page_count: number }

export type Definition = { book_id: number; title: string; page_number: number; sentence: string; pdf_link: string }

export type ConceptFigure = Figure & { title: string; book_id: number }

export type ConceptDetail = ConceptSummary & {
    books: ConceptBook[]
    definitions: Definition[]
    neighbours: Neighbour[]
    neighbour_links: { a: number; b: number; weight: number }[]
    figures: ConceptFigure[]
}

export type Evidence = { book_id: number; title: string; page_number: number; sentence: string; pdf_link: string }

export type LinkDetail = {
    a: { id: number; label: string }
    b: { id: number; label: string }
    weight: number
    sentence_count: number
    book_count: number
    relation: Relation | null
    relation_source_id: number | null
    evidence: Evidence[]
}

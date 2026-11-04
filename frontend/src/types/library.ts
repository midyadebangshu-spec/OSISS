import type { Figure } from './search'

export type LibraryBook = {
    id: number
    title: string
    author: string | null
    department: string | null
    publication_year: number | null
    language: 'en' | 'bn' | 'hi' | null
    pdf_link: string
    cover: string | null
    page_count: number | null
    chunk_count: number
    concept_count: number
    figure_count: number
    table_count: number
    has_contents: boolean
    added: string | null
}

export type TocEntry = { level: number; title: string; page: number }

export type BookConcept = { id: number; label: string; book_count: number; mentions: number; definitions: number }

export type BookDetail = LibraryBook & {
    toc: TocEntry[]
    concepts: BookConcept[]
    figures: Figure[]
}

export type BookPages = { book_id: number; page_count: number; start: number; pages: { page: number; image: string | null }[] }

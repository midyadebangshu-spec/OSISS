import type { Figure } from './search'

export type LibraryBook = {
    id: number
    title: string
    author: string | null
    department: string | null
    publication_year: number | null
    publisher: string | null
    edition: string | null
    isbn: string | null
    page_offset: number | null
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

/** One page for the reader. Word boxes are [x0, y0, x1, y1, text, line] with the box as fractions of the page. */
export type ReaderWord = [number, number, number, number, string, number]

export type ReaderPage = {
    book_id: number
    title: string
    page: number
    page_count: number
    printed_label: string | null
    citation_page: string
    image: string
    width: number
    height: number
    ocr: boolean
    words: ReaderWord[]
    previous: number | null
    next: number | null
}

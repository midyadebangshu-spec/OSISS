import type { BookDetail, BookPages, LibraryBook, ReaderPage } from '../types/library'

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(path, { signal })
    if (!response.ok) {
        let detail = ''
        try {
            detail = ((await response.json()) as { detail?: string }).detail ?? ''
        } catch {
            detail = ''
        }
        throw new Error(detail || `Request failed with status ${response.status}`)
    }
    return (await response.json()) as T
}

export async function listBooks(signal?: AbortSignal): Promise<LibraryBook[]> {
    return (await getJson<{ books: LibraryBook[] }>('/api/library', signal)).books
}

export function getBook(id: number, signal?: AbortSignal): Promise<BookDetail> {
    return getJson<BookDetail>(`/api/library/${id}`, signal)
}

export function getBookPages(id: number, start: number, count: number, signal?: AbortSignal): Promise<BookPages> {
    return getJson<BookPages>(`/api/library/${id}/pages?start=${start}&count=${count}`, signal)
}

export function getReaderPage(id: number, page: number, signal?: AbortSignal): Promise<ReaderPage> {
    return getJson<ReaderPage>(`/api/library/${id}/page/${page}`, signal)
}

/** The PDF page that carries a printed page number ("233", "xii"). */
export async function findPage(id: number, label: string, signal?: AbortSignal): Promise<number> {
    return (await getJson<{ page: number }>(`/api/library/${id}/find-page?label=${encodeURIComponent(label)}`, signal)).page
}

export const LANGUAGE_LABEL: Record<string, string> = { en: 'English', bn: 'বাংলা', hi: 'हिंदी' }

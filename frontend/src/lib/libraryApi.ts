import type { BookDetail, BookPages, LibraryBook } from '../types/library'

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

export const LANGUAGE_LABEL: Record<string, string> = { en: 'English', bn: 'বাংলা', hi: 'हिंदी' }

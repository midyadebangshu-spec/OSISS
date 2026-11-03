import type { ConceptDetail, ConceptSearch, LinkDetail } from '../types/concepts'

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

export function searchConcepts(query: string, signal?: AbortSignal, limit = 12): Promise<ConceptSearch> {
    const params = new URLSearchParams({ q: query, limit: String(limit) })
    return getJson<ConceptSearch>(`/api/concepts?${params}`, signal)
}

export function getConcept(id: number, signal?: AbortSignal): Promise<ConceptDetail> {
    return getJson<ConceptDetail>(`/api/concepts/${id}`, signal)
}

export function getLink(a: number, b: number, signal?: AbortSignal): Promise<LinkDetail> {
    return getJson<LinkDetail>(`/api/concepts/${a}/links/${b}`, signal)
}

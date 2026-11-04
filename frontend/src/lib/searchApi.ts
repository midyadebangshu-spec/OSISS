import type { Layout, SearchResponse, SearchResult } from '../types/search'

const USE_MOCK_FALLBACK = import.meta.env.VITE_USE_MOCK_FALLBACK === 'true'

const MOCK_RESPONSE: SearchResponse = {
    query: 'thermodynamics definition',
    layout: { kind: 'answer', hero: 0, intents: ['definition'] },
    results: [
        {
            exact_quote:
                'Thermodynamics is the branch of physics that deals with the relationships between heat and other forms of energy.',
            paragraph_text:
                'Thermodynamics is the branch of physics that deals with the relationships between heat and other forms of energy. It analyzes temperature, heat transfer, and work interactions in physical systems.',
            book_title: 'Foundations of Physics',
            author: 'Dr. H. C. Verma',
            department: 'Physics',
            page_number: 112,
            pdf_link: '/data/pdfs/physics_v1.pdf',
            kind: 'definition',
            blocks: [
                {
                    type: 'definition',
                    term: 'Thermodynamics',
                    text: 'Thermodynamics is the branch of physics that deals with the relationships between heat and other forms of energy.',
                },
            ],
            figures: [],
            tables: [],
            formulas: [],
            page: null,
            concepts: [],
        },
    ],
}

type ApiResult = Partial<SearchResult> & {
    quote?: string
    answer_text?: string | null
    source?: {
        book_title?: string
        author?: string
        page_number?: number
        file_path?: string
        department?: string
    }
}

type ApiResponse = {
    query?: string
    layout?: Partial<Layout>
    results?: ApiResult[]
}

function normalizeResult(item: ApiResult): SearchResult {
    return {
        exact_quote: item.exact_quote ?? item.quote ?? '',
        paragraph_text: item.paragraph_text ?? '',
        answer_text: item.answer_text ?? undefined,
        book_title: item.book_title ?? item.source?.book_title ?? 'Unknown Title',
        author: item.author ?? item.source?.author ?? 'Unknown Author',
        department: item.department ?? item.source?.department,
        page_number: item.page_number ?? item.source?.page_number ?? 0,
        pdf_link: item.pdf_link ?? item.source?.file_path ?? '#',
        kind: item.kind ?? 'text',
        blocks: item.blocks ?? [],
        figures: item.figures ?? [],
        tables: item.tables ?? [],
        formulas: item.formulas ?? [],
        page: item.page ?? null,
        concepts: item.concepts ?? [],
        book_id: item.book_id ?? null,
        printed_page: item.printed_page ?? null,
    }
}

function normalizeResponse(payload: ApiResponse, query: string): SearchResponse {
    const results = (payload.results ?? []).map(normalizeResult)
    return {
        query: payload.query ?? query,
        layout: {
            kind: payload.layout?.kind ?? (results.length ? 'answer' : 'empty'),
            hero: payload.layout?.hero ?? (results.length ? 0 : null),
            intents: payload.layout?.intents ?? [],
        },
        results,
    }
}

/** A single book id, a list of book ids (a project's books), or undefined for the whole library. */
export type SearchScope = number | number[] | undefined

function scopeBody(scope: SearchScope): Record<string, unknown> {
    if (Array.isArray(scope)) return { book_ids: scope }
    return scope ? { book_id: scope } : {}
}

async function post(path: string, query: string, scope?: SearchScope): Promise<Response> {
    return fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, ...scopeBody(scope) }),
    })
}

/** Search the library, or only inside one book or a set of books. */
export async function searchRecords(query: string, scope?: SearchScope): Promise<SearchResponse> {
    try {
        let response = await post('/api/search/rich', query, scope)
        if (response.status === 404 && !scope) {
            // Older API without rich results: plain answers, laid out as a list.
            response = await post('/api/search', query)
        }
        if (!response.ok) {
            let detail = ''
            try {
                detail = ((await response.json()) as { detail?: string }).detail ?? ''
            } catch {
                detail = ''
            }
            throw new Error(detail || `Search endpoint returned status ${response.status}`)
        }
        return normalizeResponse((await response.json()) as ApiResponse, query)
    } catch (error) {
        if (!USE_MOCK_FALLBACK) {
            throw error instanceof Error
                ? error
                : new Error('Search API is unavailable. Start backend API or enable VITE_USE_MOCK_FALLBACK=true.')
        }

        await new Promise((resolve) => setTimeout(resolve, 400))
        return { ...MOCK_RESPONSE, query }
    }
}

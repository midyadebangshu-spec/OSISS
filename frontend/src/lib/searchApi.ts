import type { SearchResponse } from '../types/search'

const USE_MOCK_FALLBACK = import.meta.env.VITE_USE_MOCK_FALLBACK === 'true'

const MOCK_RESPONSE: SearchResponse = {
    query: 'thermodynamics definition',
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
        },
    ],
}

type ApiResultShape = {
    query?: string
    results?: Array<{
        exact_quote?: string
        paragraph_text?: string
        quote?: string
        book_title?: string
        author?: string
        department?: string
        page_number?: number
        pdf_link?: string
        source?: {
            book_title?: string
            author?: string
            page_number?: number
            file_path?: string
            department?: string
        }
    }>
}

function normalizeResponse(payload: ApiResultShape, query: string): SearchResponse {
    const normalizedResults = (payload.results ?? []).map((item) => ({
        exact_quote: item.exact_quote ?? item.quote ?? '',
        paragraph_text: item.paragraph_text ?? '',
        book_title: item.book_title ?? item.source?.book_title ?? 'Unknown Title',
        author: item.author ?? item.source?.author ?? 'Unknown Author',
        department: item.department ?? item.source?.department,
        page_number: item.page_number ?? item.source?.page_number ?? 0,
        pdf_link: item.pdf_link ?? item.source?.file_path ?? '#',
    }))

    return {
        query: payload.query ?? query,
        results: normalizedResults,
    }
}

export async function searchRecords(query: string): Promise<SearchResponse> {
    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ query }),
        })

        if (!response.ok) {
            throw new Error(`Search endpoint returned status ${response.status}`)
        }

        const payload = (await response.json()) as ApiResultShape
        return normalizeResponse(payload, query)
    } catch (error) {
        if (!USE_MOCK_FALLBACK) {
            throw error instanceof Error
                ? error
                : new Error('Search API is unavailable. Start backend API or enable VITE_USE_MOCK_FALLBACK=true.')
        }

        await new Promise((resolve) => setTimeout(resolve, 400))
        return {
            ...MOCK_RESPONSE,
            query,
        }
    }
}

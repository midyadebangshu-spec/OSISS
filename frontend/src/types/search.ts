export type SearchResult = {
    exact_quote: string
    paragraph_text: string
    book_title: string
    author: string
    department?: string
    page_number: number
    pdf_link: string
}

export type SearchResponse = {
    query: string
    results: SearchResult[]
}

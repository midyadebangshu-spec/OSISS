import { Download, PanelRightOpen } from 'lucide-react'
import type { SearchResult } from '../types/search'

type ResultCardProps = {
    result: SearchResult
    onViewPage: (result: SearchResult) => void
    isPreviewOpen: boolean
}

export function ResultCard({ result, onViewPage, isPreviewOpen }: ResultCardProps) {
    return (
        <article className="border-2 border-zinc-950 bg-white p-5">
            <div className="mb-4 flex items-center justify-between gap-2">
                <span className="text-xs uppercase tracking-[0.18em] text-zinc-500">Database Hit</span>
            </div>

            <blockquote className="border-l-4 border-zinc-950 bg-zinc-100 px-4 py-3 text-base leading-relaxed">
                “{result.exact_quote}”
            </blockquote>

            <div className="mt-4 border border-zinc-300 bg-zinc-50 px-4 py-3 text-sm leading-relaxed">
                <p className="mb-2 text-xs uppercase tracking-[0.14em] text-zinc-500">Matched Paragraph</p>
                <p>{result.paragraph_text || 'Paragraph text unavailable for this hit.'}</p>
            </div>

            <dl className="mt-4 grid grid-cols-1 gap-2 text-sm md:grid-cols-2">
                <div>
                    <dt className="text-zinc-500">Book Title</dt>
                    <dd className="font-semibold text-zinc-950">{result.book_title}</dd>
                </div>
                <div>
                    <dt className="text-zinc-500">Author</dt>
                    <dd>{result.author}</dd>
                </div>
                {result.department && (
                    <div>
                        <dt className="text-zinc-500">Department</dt>
                        <dd>{result.department}</dd>
                    </div>
                )}
                <div>
                    <dt className="text-zinc-500">Page Number</dt>
                    <dd>{result.page_number}</dd>
                </div>
            </dl>

            <div className="mt-5 flex flex-wrap items-center gap-4 text-sm">
                <button
                    type="button"
                    onClick={() => onViewPage(result)}
                    className="inline-flex items-center gap-1 underline-offset-4 hover:underline"
                >
                    <PanelRightOpen className="h-4 w-4" />
                    {isPreviewOpen ? 'Hide Page' : 'View Page'}
                </button>
                <a href={result.pdf_link} className="inline-flex items-center gap-1 underline-offset-4 hover:underline" download>
                    <Download className="h-4 w-4" />
                    Download PDF
                </a>
            </div>
        </article>
    )
}

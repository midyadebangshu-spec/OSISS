import { Download, PanelRightOpen } from 'lucide-react'
import type { SearchResult } from '../types/search'
import { buildParagraphSegments } from '../lib/textFormat'
import type { Segment } from '../lib/textFormat'

type ResultCardProps = {
    result: SearchResult
    onViewPage: (result: SearchResult) => void
    isPreviewOpen: boolean
}

const SEGMENT_CLASS: Record<Segment['kind'], string> = {
    context: 'text-zinc-500',
    quote: 'bg-amber-100 text-zinc-950',
    answer: 'bg-amber-300 font-semibold text-zinc-950',
}

function Highlighted({ segments }: { segments: Segment[] }) {
    return (
        <>
            {segments.map((segment, index) => (
                <span key={index} className={SEGMENT_CLASS[segment.kind]}>
                    {segment.text}
                </span>
            ))}
        </>
    )
}

export function ResultCard({ result, onViewPage, isPreviewOpen }: ResultCardProps) {
    const paragraph = buildParagraphSegments(result.paragraph_text, result.exact_quote, result.answer_text)

    return (
        <article className="border-2 border-zinc-950 bg-white p-5">
            <div className="mb-4 flex items-center justify-between gap-2">
                <span className="text-xs uppercase tracking-[0.18em] text-zinc-500">Database Hit</span>
                <span className="text-xs text-zinc-500">Page {result.page_number}</span>
            </div>

            <div className="border border-zinc-300 bg-zinc-50 px-4 py-3 text-sm leading-relaxed">
                <p className="mb-2 text-xs uppercase tracking-[0.14em] text-zinc-500">Matched Paragraph</p>
                {paragraph.length > 0 ? (
                    <p className="whitespace-pre-line">
                        <span className="text-zinc-400">… </span>
                        <Highlighted segments={paragraph} />
                        <span className="text-zinc-400"> …</span>
                    </p>
                ) : (
                    <p>Paragraph text unavailable for this hit.</p>
                )}
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

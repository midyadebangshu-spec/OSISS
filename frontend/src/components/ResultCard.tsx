import { Download, PanelRightOpen } from 'lucide-react'
import type { SearchResult } from '../types/search'
import { buildParagraphSegments } from '../lib/textFormat'
import type { Segment } from '../lib/textFormat'
import { KIND_LABEL, figureGallery, hasLead, sourceLabel } from '../lib/results'
import { useViewer } from '../lib/viewer'
import { CodeView, DefinitionView, ListView } from './blocks/ContentBlocks'
import { FigureView, FormulaStrip, PageCard, TableView } from './blocks/MediaBlocks'

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

/** The matched paragraph with the quote and the exact answer highlighted. */
export function MatchedParagraph({ result, label = 'Matched paragraph', muted = false }: { result: SearchResult; label?: string; muted?: boolean }) {
    const paragraph = buildParagraphSegments(result.paragraph_text, result.exact_quote, result.answer_text)
    return (
        <div className={`border px-4 py-3 text-sm leading-relaxed ${muted ? 'border-zinc-200 bg-white' : 'border-zinc-300 bg-zinc-50'}`}>
            <p className="mb-2 text-xs uppercase tracking-[0.14em] text-zinc-500">{label}</p>
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
    )
}

/** The block a result leads with (definition / list / code), or null when it is plain text. */
export function LeadBlock({ result, large = false }: { result: SearchResult; large?: boolean }) {
    const code = result.blocks.find((block) => block.type === 'code')
    const definition = result.blocks.find((block) => block.type === 'definition')
    const list = result.blocks.find((block) => block.type === 'list')
    if (result.kind === 'code' && code) return <CodeView block={code} />
    if (definition && (result.kind === 'definition' || !list)) {
        return (
            <div className="space-y-4">
                <DefinitionView block={definition} large={large} />
                {list && <ListView block={list} large={large} />}
            </div>
        )
    }
    if (list) return <ListView block={list} large={large} />
    if (code) return <CodeView block={code} />
    return null
}

export function SourceFooter({ result }: { result: SearchResult }) {
    const { togglePage, isPageOpen } = useViewer()
    const open = isPageOpen(result.pdf_link, result.page_number)
    return (
        <div className="flex flex-col gap-3 border-t border-zinc-300 pt-3 text-sm md:flex-row md:items-end md:justify-between">
            <div className="min-w-0">
                <p className="truncate font-semibold text-zinc-950" title={result.book_title}>
                    {result.book_title}
                </p>
                <p className="text-zinc-600">
                    {result.author}
                    {result.department && <span> · {result.department}</span>}
                    <span> · Page {result.page_number}</span>
                </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-4">
                <button
                    type="button"
                    onClick={() => togglePage({ pdfLink: result.pdf_link, pageNumber: result.page_number, title: result.book_title })}
                    className="inline-flex items-center gap-1 underline-offset-4 hover:underline"
                >
                    <PanelRightOpen className="h-4 w-4" />
                    {open ? 'Hide PDF' : 'Open in PDF'}
                </button>
                <a href={result.pdf_link} className="inline-flex items-center gap-1 underline-offset-4 hover:underline" download>
                    <Download className="h-4 w-4" />
                    Download
                </a>
            </div>
        </div>
    )
}

type ResultCardProps = {
    result: SearchResult
    compact?: boolean
    /** Leave out figures/tables already shown elsewhere in the layout (e.g. the figure gallery). */
    hideFigures?: boolean
    hideTables?: boolean
}

/** One hit: what it leads with, the passage, related figures / tables / equations, and the page. */
export function ResultCard({ result, compact = false, hideFigures = false, hideTables = false }: ResultCardProps) {
    const lead = hasLead(result)
    const source = sourceLabel(result)
    const figures = hideFigures ? [] : result.figures
    const tables = hideTables ? [] : result.tables.filter((table) => table.primary)
    const gallery = figureGallery(result)
    const showPage = !compact && result.page && figures.length === 0

    return (
        <article className="flex flex-col gap-4 border-2 border-zinc-950 bg-white p-4 md:p-5">
            <div className="flex items-center justify-between gap-2 text-xs uppercase tracking-[0.18em] text-zinc-500">
                <span className="border border-zinc-950 px-2 py-0.5 text-zinc-950">{KIND_LABEL[result.kind]}</span>
                <span>Page {result.page_number}</span>
            </div>

            <div className={showPage ? 'grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]' : 'flex flex-col gap-4'}>
                <div className="flex min-w-0 flex-col gap-4">
                    {lead && <LeadBlock result={result} />}
                    <MatchedParagraph result={result} label={lead ? 'From the passage' : 'Matched paragraph'} muted={lead} />
                    <FormulaStrip formulas={result.formulas} source={source} />
                </div>
                {showPage && result.page && (
                    <PageCard page={result.page} title={result.book_title} source={source} pageNumber={result.page_number} />
                )}
            </div>

            {figures.length > 0 && (
                <div className={`grid gap-3 ${compact ? 'grid-cols-2' : 'grid-cols-2 md:grid-cols-3'}`}>
                    {figures.map((figure, index) => (
                        <FigureView
                            key={figure.image}
                            figure={figure}
                            source={source}
                            gallery={gallery}
                            index={index}
                            size={compact || !figure.primary ? 'thumb' : 'normal'}
                        />
                    ))}
                    {!compact && result.page && (
                        <PageThumb result={result} />
                    )}
                </div>
            )}

            {tables.map((table) => (
                <TableView key={table.image} table={table} source={source} />
            ))}

            <SourceFooter result={result} />
        </article>
    )
}

function PageThumb({ result }: { result: SearchResult }) {
    const { openLightbox } = useViewer()
    if (!result.page) return null
    const page = result.page
    return (
        <button
            type="button"
            onClick={() => openLightbox([{ kind: 'page', page, title: result.book_title, source: sourceLabel(result) }], 0)}
            className="flex flex-col border-2 border-zinc-950 bg-white text-left"
        >
            <span className="flex h-28 items-start justify-center overflow-hidden bg-zinc-100">
                <img src={page.image} alt={`Page ${result.page_number}`} className="w-full object-cover object-top" loading="lazy" />
            </span>
            <span className="truncate border-t border-zinc-300 px-2 py-1 text-xs text-zinc-600">Whole page {result.page_number}</span>
        </button>
    )
}

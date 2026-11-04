import type { ReactNode } from 'react'
import type { Layout, SearchResult } from '../../types/search'
import { buildParagraphSegments } from '../../lib/textFormat'
import type { LightboxItem } from '../../lib/viewer'
import { KIND_LABEL, figureGallery, figureLightboxItem, hasLead, sourceLabel } from '../../lib/results'
import { FigureView, FormulaStrip, PageCard, TableView } from '../blocks/MediaBlocks'
import { LeadBlock, MatchedParagraph, ResultCard, SourceFooter } from '../ResultCard'
import { FlashcardTarget } from '../FlashcardMenu'
import { cardFromResult } from '../../lib/flashcardText'
import { SearchQueryContext, useSearchQuery } from '../../lib/searchQuery'

const LAYOUT_TITLE: Record<Layout['kind'], string> = {
    answer: 'Best answer',
    steps: 'Step by step',
    visual: 'Figures from the books',
    table: 'Table from the books',
    compare: 'Side by side',
    empty: 'No results',
}

function SectionTitle({ children, note }: { children: ReactNode; note?: string }) {
    return (
        <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight">{children}</h2>
            {note && <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">{note}</p>}
        </div>
    )
}

/** The quoted sentence set large, with the exact answer marked, for hits without a structured block. */
function BigQuote({ result }: { result: SearchResult }) {
    const segments = buildParagraphSegments(result.exact_quote, result.exact_quote, result.answer_text)
    return (
        <blockquote className="border-l-4 border-zinc-950 pl-4 text-xl leading-relaxed md:text-2xl">
            {segments.map((segment, index) => (
                <span key={index} className={segment.kind === 'answer' ? 'bg-amber-300 font-semibold' : ''}>
                    {segment.text}
                </span>
            ))}
        </blockquote>
    )
}

function HeroFrame({ label, result, children }: { label: string; result: SearchResult; children: ReactNode }) {
    const query = useSearchQuery()
    return (
        <FlashcardTarget getCard={() => cardFromResult(result, query)}>
        <section className="border-2 border-zinc-950 bg-white">
            <header className="flex flex-wrap items-center justify-between gap-2 bg-zinc-950 px-4 py-2 text-xs uppercase tracking-[0.18em] text-white">
                <span>{label}</span>
                <span className="text-zinc-400">{KIND_LABEL[result.kind]}</span>
            </header>
            <div className="flex flex-col gap-5 p-4 md:p-6">
                {children}
                <SourceFooter result={result} />
            </div>
        </section>
        </FlashcardTarget>
    )
}

/** The leading hit: its answer block next to its main figure (or the page with the sentence marked). */
function HeroResult({ result, label }: { result: SearchResult; label: string }) {
    const source = sourceLabel(result)
    const gallery = figureGallery(result)
    const mainFigure = result.figures.find((figure) => figure.primary)
    const otherFigures = result.figures.filter((figure) => figure !== mainFigure)
    const side = mainFigure ? (
        <FigureView figure={mainFigure} source={source} gallery={gallery} index={result.figures.indexOf(mainFigure)} size="hero" />
    ) : result.page ? (
        <PageCard page={result.page} title={result.book_title} source={source} pageNumber={result.page_number} />
    ) : null
    const wideLead = result.kind === 'code'

    return (
        <HeroFrame label={label} result={result}>
            <div className={side && !wideLead ? 'grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]' : 'flex flex-col gap-5'}>
                <div className="flex min-w-0 flex-col gap-5">
                    {hasLead(result) ? <LeadBlock result={result} large /> : <BigQuote result={result} />}
                    <MatchedParagraph result={result} label="In context" muted />
                    <FormulaStrip formulas={result.formulas} source={source} />
                </div>
                {side && <div className={wideLead ? 'max-w-xl' : ''}>{side}</div>}
            </div>
            {result.tables.filter((table) => table.primary).map((table) => (
                <TableView key={table.image} table={table} source={source} />
            ))}
            {(otherFigures.length > 0 || (mainFigure && result.page)) && (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                    {otherFigures.map((figure) => (
                        <FigureView key={figure.image} figure={figure} source={source} gallery={gallery} index={result.figures.indexOf(figure)} size="thumb" />
                    ))}
                    {mainFigure && result.page && (
                        <div className="col-span-2 sm:col-span-1">
                            <PageCard page={result.page} title={result.book_title} source={source} pageNumber={result.page_number} />
                        </div>
                    )}
                </div>
            )}
        </HeroFrame>
    )
}

function MoreResults({ results, title = 'More from the library', hideFigures = false }: { results: SearchResult[]; title?: string; hideFigures?: boolean }) {
    if (results.length === 0) return null
    return (
        <section className="space-y-3">
            <SectionTitle note={`${results.length} more`}>{title}</SectionTitle>
            {results.map((result, index) => (
                <ResultCard key={`${result.pdf_link}-${result.page_number}-${index}`} result={result} hideFigures={hideFigures} />
            ))}
        </section>
    )
}

/** Figure-led: the most relevant figure large, every other figure from the hits in a gallery. */
function VisualLayout({ results, hero }: { results: SearchResult[]; hero: number }) {
    const lead = results[hero]
    const all: { item: LightboxItem; figure: SearchResult['figures'][number]; result: SearchResult }[] = []
    for (const result of [lead, ...results.filter((_, index) => index !== hero)]) {
        for (const figure of result.figures) {
            all.push({ item: figureLightboxItem(figure, sourceLabel(result)), figure, result })
        }
    }
    const gallery = all.map((entry) => entry.item)
    const [first, ...rest] = all
    if (!first) return <AnswerLayout results={results} hero={hero} label={LAYOUT_TITLE.answer} />

    return (
        <div className="space-y-6">
            <HeroFrame label={LAYOUT_TITLE.visual} result={first.result}>
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
                    <FigureView figure={first.figure} source={sourceLabel(first.result)} gallery={gallery} index={0} size="hero" />
                    <div className="flex min-w-0 flex-col gap-4">
                        {hasLead(first.result) && <LeadBlock result={first.result} />}
                        <MatchedParagraph result={first.result} label="What the book says" />
                    </div>
                </div>
            </HeroFrame>
            {rest.length > 0 && (
                <section className="space-y-3">
                    <SectionTitle note={`${rest.length} more`}>More figures</SectionTitle>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                        {rest.map((entry, index) => (
                            <div key={entry.figure.image} className="flex flex-col gap-1">
                                <FigureView figure={entry.figure} source={sourceLabel(entry.result)} gallery={gallery} index={index + 1} />
                                <p className="truncate text-xs text-zinc-600" title={entry.result.book_title}>
                                    {sourceLabel(entry.result)}
                                </p>
                            </div>
                        ))}
                    </div>
                </section>
            )}
            <MoreResults results={results.filter((result) => result !== first.result)} title="Passages" hideFigures />
        </div>
    )
}

/** Table-led: the table that answers a comparison, then the passages. */
function TableLayout({ results, hero }: { results: SearchResult[]; hero: number }) {
    const lead = results[hero]
    const source = sourceLabel(lead)
    const table = lead.tables.find((item) => item.primary) ?? lead.tables[0]
    return (
        <div className="space-y-6">
            <HeroFrame label={LAYOUT_TITLE.table} result={lead}>
                <TableView table={table} source={source} />
                <div className="grid gap-5 lg:grid-cols-2">
                    {hasLead(lead) ? <LeadBlock result={lead} /> : <BigQuote result={lead} />}
                    <MatchedParagraph result={lead} label="In context" muted />
                </div>
            </HeroFrame>
            <MoreResults results={results.filter((_, index) => index !== hero)} />
        </div>
    )
}

/** Comparison without a table: the best passage from each book next to each other. */
function CompareLayout({ results }: { results: SearchResult[] }) {
    const seen = new Set<string>()
    const columns: SearchResult[] = []
    for (const result of results) {
        // By file: two different books can share a (guessed) title.
        if (!seen.has(result.pdf_link) && columns.length < 3) {
            seen.add(result.pdf_link)
            columns.push(result)
        }
    }
    const rest = results.filter((result) => !columns.includes(result))
    return (
        <div className="space-y-6">
            <section className="space-y-3">
                <SectionTitle note={`${columns.length} books`}>{LAYOUT_TITLE.compare}</SectionTitle>
                <div className={`grid gap-4 ${columns.length >= 3 ? 'md:grid-cols-2 2xl:grid-cols-3' : 'md:grid-cols-2'}`}>
                    {columns.map((result, index) => (
                        <ResultCard key={`${result.pdf_link}-${index}`} result={result} compact />
                    ))}
                </div>
            </section>
            <MoreResults results={rest} />
        </div>
    )
}

function AnswerLayout({ results, hero, label }: { results: SearchResult[]; hero: number; label: string }) {
    return (
        <div className="space-y-6">
            <HeroResult result={results[hero]} label={label} />
            <MoreResults results={results.filter((_, index) => index !== hero)} />
        </div>
    )
}

/** Arranges the hits according to the layout the API suggested (see src/layout.py). */
export function ResultsView({ results, layout, query = '' }: { results: SearchResult[]; layout: Layout; query?: string }) {
    return (
        <SearchQueryContext.Provider value={query}>
            <LaidOut results={results} layout={layout} />
        </SearchQueryContext.Provider>
    )
}

function LaidOut({ results, layout }: { results: SearchResult[]; layout: Layout }) {
    const hero = layout.hero !== null && layout.hero < results.length ? layout.hero : 0
    switch (layout.kind) {
        case 'visual':
            return <VisualLayout results={results} hero={hero} />
        case 'table':
            return results[hero].tables.length ? <TableLayout results={results} hero={hero} /> : <AnswerLayout results={results} hero={hero} label={LAYOUT_TITLE.answer} />
        case 'compare':
            return <CompareLayout results={results} />
        case 'steps':
            return <AnswerLayout results={results} hero={hero} label={LAYOUT_TITLE.steps} />
        default:
            return <AnswerLayout results={results} hero={hero} label={LAYOUT_TITLE.answer} />
    }
}

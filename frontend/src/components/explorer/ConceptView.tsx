import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, BookOpen, ExternalLink, Quote } from 'lucide-react'
import type { ConceptDetail, LinkDetail, Neighbour } from '../../types/concepts'
import { getLink } from '../../lib/conceptsApi'
import { exploreHref, navigate } from '../../lib/route'
import { orderPhrase, relationPhrase } from '../../lib/relations'
import { figureLightboxItem } from '../../lib/results'
import { useViewer } from '../../lib/viewer'
import { FigureView } from '../blocks/MediaBlocks'
import { ConceptGraph } from './ConceptGraph'

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The sentence with the given terms set in bold (case-insensitive, longest first). */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
    const usable = [...new Set(terms.filter((term) => term.length >= 2))].sort((a, b) => b.length - a.length)
    if (usable.length === 0) return <>{text}</>
    const parts = text.split(new RegExp(`(${usable.map(escapeRegExp).join('|')})`, 'giu'))
    return (
        <>
            {parts.map((part, index) =>
                usable.some((term) => term.toLowerCase() === part.toLowerCase()) ? (
                    <mark key={index} className="bg-amber-200 px-0.5 font-semibold text-zinc-950">
                        {part}
                    </mark>
                ) : (
                    <span key={index}>{part}</span>
                ),
            )}
        </>
    )
}

function SourceLink({ title, page, pdfLink }: { title: string; page: number; pdfLink: string }) {
    const { togglePage } = useViewer()
    return (
        <button
            type="button"
            onClick={() => togglePage({ pdfLink, pageNumber: page, title })}
            className="inline-flex max-w-full items-center gap-1 text-left text-xs text-zinc-600 underline-offset-4 hover:text-zinc-950 hover:underline"
        >
            <BookOpen className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
                {title} · p. {page}
            </span>
        </button>
    )
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
    return (
        <section className="space-y-3">
            <div className="flex items-baseline justify-between gap-3">
                <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
                {note && <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">{note}</p>}
            </div>
            {children}
        </section>
    )
}

function LinkPanel({ concept, neighbour }: { concept: ConceptDetail; neighbour: Neighbour }) {
    // Results are tagged with the neighbour they belong to, so a stale one is never shown for another link.
    const [loaded, setLoaded] = useState<{ id: number; link: LinkDetail | null; error: string | null } | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        getLink(concept.id, neighbour.id, controller.signal)
            .then((link) => setLoaded({ id: neighbour.id, link, error: null }))
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) {
                    setLoaded({ id: neighbour.id, link: null, error: reason instanceof Error ? reason.message : 'Could not load the link.' })
                }
            })
        return () => controller.abort()
    }, [concept.id, neighbour.id])
    const current = loaded?.id === neighbour.id ? loaded : null
    const link = current?.link ?? null
    const error = current?.error ?? null

    const terms = [concept.label, ...concept.aliases, neighbour.label, ...(neighbour.aliases ?? [])]
    const order = orderPhrase(neighbour.order)
    return (
        <div className="flex h-full flex-col">
            <div className="border-b-2 border-zinc-950 p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">Link</p>
                <p className="mt-1 text-lg leading-snug">
                    <span className="font-semibold">{concept.label}</span>{' '}
                    <span className="text-zinc-600">{relationPhrase(neighbour)}</span>{' '}
                    <span className="font-semibold">{neighbour.label}</span>
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                    Named together in {neighbour.sentence_count} sentence{neighbour.sentence_count === 1 ? '' : 's'} across{' '}
                    {neighbour.shared_books} book{neighbour.shared_books === 1 ? '' : 's'}
                    {order && ` · ${neighbour.label} is ${order}`}
                </p>
                <a
                    href={exploreHref(neighbour.id)}
                    className="mt-3 inline-flex items-center gap-1.5 border-2 border-zinc-950 bg-zinc-950 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800"
                >
                    Explore {neighbour.label}
                    <ArrowRight className="h-4 w-4" />
                </a>
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">Where the books connect them</p>
                {error && <p className="text-sm text-zinc-700">{error}</p>}
                {!link && !error && <p className="text-sm text-zinc-500">Loading…</p>}
                {link?.evidence.map((item, index) => (
                    <figure key={index} className="border-l-4 border-zinc-300 pl-3">
                        <blockquote className="text-sm leading-relaxed">
                            <Highlighted text={item.sentence} terms={terms} />
                        </blockquote>
                        <figcaption className="mt-1">
                            <SourceLink title={item.title} page={item.page_number} pdfLink={item.pdf_link} />
                        </figcaption>
                    </figure>
                ))}
            </div>
        </div>
    )
}

function NeighbourList({ concept, onSelect }: { concept: ConceptDetail; onSelect: (id: number) => void }) {
    return (
        <div className="flex h-full flex-col">
            <div className="border-b-2 border-zinc-950 p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">Related concepts</p>
                <p className="mt-1 text-sm text-zinc-600">Select one to see the sentences that connect it to {concept.label}.</p>
            </div>
            <ul className="flex-1 divide-y divide-zinc-200 overflow-y-auto">
                {concept.neighbours.map((n) => (
                    <li key={n.id}>
                        <button type="button" onClick={() => onSelect(n.id)} className="flex w-full items-baseline justify-between gap-3 px-4 py-2 text-left hover:bg-zinc-50">
                            <span className="min-w-0">
                                <span className="text-xs text-zinc-500">{relationPhrase(n)} </span>
                                <span className="font-medium">{n.label}</span>
                            </span>
                            <span className="shrink-0 text-xs text-zinc-500">{n.shared_books} book{n.shared_books === 1 ? '' : 's'}</span>
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    )
}

/** Everything the corpus says about one concept: names, links, definitions per book, figures, where it appears. */
export function ConceptView({ concept }: { concept: ConceptDetail }) {
    const [selectedId, setSelectedId] = useState<number | null>(null)
    const { togglePage } = useViewer()
    const selected = concept.neighbours.find((n) => n.id === selectedId) ?? null
    const otherNames = concept.aliases.filter((alias) => alias.toLowerCase() !== concept.label.toLowerCase())
    const gallery = useMemo(
        () => concept.figures.map((figure) => figureLightboxItem(figure, `${figure.title} · p. ${figure.page_number}`)),
        [concept.figures],
    )
    const definedIn = new Set(concept.definitions.map((d) => d.book_id)).size

    return (
        <div className="space-y-8">
            <header className="space-y-2 border-b-2 border-zinc-950 pb-4">
                <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">Concept</p>
                <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">{concept.label}</h1>
                {otherNames.length > 0 && (
                    <p className="flex flex-wrap gap-2 text-sm">
                        <span className="text-zinc-500">Also:</span>
                        {otherNames.slice(0, 8).map((alias) => (
                            <span key={alias} className="border border-zinc-300 px-2 py-0.5">
                                {alias}
                            </span>
                        ))}
                    </p>
                )}
                <p className="text-sm text-zinc-600">
                    In {concept.book_count} book{concept.book_count === 1 ? '' : 's'} · defined in {definedIn} · {concept.neighbours.length} related
                    concept{concept.neighbours.length === 1 ? '' : 's'}
                </p>
            </header>

            {concept.neighbours.length > 0 ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
                    <div className="min-w-0 border-2 border-zinc-950 bg-white p-3">
                        <ConceptGraph
                            concept={concept}
                            selectedId={selectedId}
                            onSelect={(id) => setSelectedId((current) => (current === id ? null : id))}
                            onOpen={(id) => navigate(exploreHref(id))}
                        />
                    </div>
                    <div className="max-h-[34rem] min-h-[20rem] min-w-0 border-2 border-zinc-950 bg-white">
                        {selected ? <LinkPanel concept={concept} neighbour={selected} /> : <NeighbourList concept={concept} onSelect={setSelectedId} />}
                    </div>
                </div>
            ) : (
                <p className="border-2 border-zinc-950 p-4 text-sm text-zinc-700">
                    No sentence in the books names {concept.label} together with another concept often enough to link them.
                </p>
            )}

            {concept.definitions.length > 0 && (
                <Section title="How the books define it" note={`${definedIn} book${definedIn === 1 ? '' : 's'}`}>
                    <div className="grid gap-3 md:grid-cols-2">
                        {concept.definitions.map((definition, index) => (
                            <figure key={index} className="flex flex-col justify-between gap-3 border-2 border-zinc-950 p-4">
                                <blockquote className="flex gap-2 leading-relaxed">
                                    <Quote className="mt-1 h-4 w-4 shrink-0 text-zinc-400" />
                                    <span>
                                        <Highlighted text={definition.sentence} terms={[concept.label, ...concept.aliases]} />
                                    </span>
                                </blockquote>
                                <figcaption>
                                    <SourceLink title={definition.title} page={definition.page_number} pdfLink={definition.pdf_link} />
                                </figcaption>
                            </figure>
                        ))}
                    </div>
                </Section>
            )}

            {concept.figures.length > 0 && (
                <Section title="Figures" note={`${concept.figures.length} with ${concept.label} in the caption`}>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                        {concept.figures.map((figure, index) => (
                            <div key={`${figure.image}-${index}`} className="flex flex-col gap-1">
                                <FigureView figure={figure} source={`${figure.title} · p. ${figure.page_number}`} gallery={gallery} index={index} />
                                <p className="truncate text-xs text-zinc-600" title={figure.title}>
                                    {figure.title} · p. {figure.page_number}
                                </p>
                            </div>
                        ))}
                    </div>
                </Section>
            )}

            <Section title="Where it appears" note={`${concept.books.length} book${concept.books.length === 1 ? '' : 's'}`}>
                <ul className="divide-y divide-zinc-200 border-2 border-zinc-950">
                    {concept.books.map((book) => (
                        <li key={book.book_id} className="flex flex-col gap-2 p-3 md:flex-row md:items-start md:justify-between">
                            <div className="min-w-0">
                                <p className="font-medium">{book.title}</p>
                                <p className="text-xs text-zinc-500">
                                    {book.page_count} page{book.page_count === 1 ? '' : 's'}
                                </p>
                            </div>
                            <div className="flex flex-wrap gap-1 md:max-w-[60%] md:justify-end">
                                {book.pages.slice(0, 24).map((page) => (
                                    <button
                                        key={page}
                                        type="button"
                                        onClick={() => togglePage({ pdfLink: book.pdf_link, pageNumber: page, title: book.title })}
                                        className="min-w-9 border border-zinc-300 px-1.5 py-0.5 text-xs hover:border-zinc-950 hover:bg-zinc-50"
                                        aria-label={`Open ${book.title} at page ${page}`}
                                    >
                                        {page}
                                    </button>
                                ))}
                                {book.page_count > 24 && <span className="px-1 text-xs text-zinc-500">+{book.page_count - 24} more</span>}
                                <a href={book.pdf_link} className="inline-flex items-center gap-1 px-1 text-xs underline-offset-4 hover:underline" target="_blank" rel="noreferrer">
                                    <ExternalLink className="h-3 w-3" /> PDF
                                </a>
                            </div>
                        </li>
                    ))}
                </ul>
            </Section>
        </div>
    )
}

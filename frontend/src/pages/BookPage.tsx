import { useEffect, useMemo, useState } from 'react'
import { useAuth } from '../lib/auth'
import { recordActivity } from '../lib/workspaceApi'
import type { FormEvent } from 'react'
import { ArrowLeft, BookOpen, Download, ListTree, Network, Search } from 'lucide-react'
import { FigureView } from '../components/blocks/MediaBlocks'
import { BookCover } from '../components/library/BookCover'
import { ResultsView } from '../components/layouts/ResultsView'
import { ResultSkeleton } from '../components/ResultSkeleton'
import { LANGUAGE_LABEL, getBook, getBookPages } from '../lib/libraryApi'
import { figureLightboxItem } from '../lib/results'
import { exploreHref, libraryHref, readerHref } from '../lib/route'
import { searchRecords } from '../lib/searchApi'
import { useViewer } from '../lib/viewer'
import type { LightboxItem } from '../lib/viewer'
import type { BookDetail, BookPages } from '../types/library'
import type { SearchResponse } from '../types/search'

const PAGE_BATCH = 24

function Section({ title, icon, note, children }: { title: string; icon?: React.ReactNode; note?: string; children: React.ReactNode }) {
    return (
        <section className="space-y-3">
            <div className="flex items-baseline justify-between gap-3 border-b border-zinc-300 pb-1">
                <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
                    {icon}
                    {title}
                </h2>
                {note && <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">{note}</p>}
            </div>
            {children}
        </section>
    )
}

function Stat({ value, label }: { value: number | null; label: string }) {
    if (value === null || value === 0) return null
    return (
        <div className="border border-zinc-300 px-3 py-2">
            <p className="text-lg font-semibold">{value.toLocaleString()}</p>
            <p className="text-xs uppercase tracking-[0.14em] text-zinc-500">{label}</p>
        </div>
    )
}

function Contents({ book }: { book: BookDetail }) {
    const [expanded, setExpanded] = useState(false)
    const entries = expanded ? book.toc : book.toc.slice(0, 40)
    return (
        <Section title="Contents" icon={<ListTree className="h-5 w-5" />} note={`${book.toc.length} entries`}>
            <ol className="max-h-[32rem] divide-y divide-zinc-100 overflow-y-auto border-2 border-zinc-950">
                {entries.map((entry, index) => (
                    <li key={index}>
                        <a
                            href={readerHref(book.id, entry.page)}
                            className="flex w-full items-baseline justify-between gap-3 px-3 py-1.5 text-left hover:bg-zinc-50"
                            style={{ paddingLeft: `${0.75 + (entry.level - 1) * 1.25}rem` }}
                        >
                            <span className={entry.level === 1 ? 'font-medium' : 'text-sm text-zinc-700'}>{entry.title}</span>
                            <span className="shrink-0 text-xs text-zinc-500">p. {entry.page}</span>
                        </a>
                    </li>
                ))}
            </ol>
            {book.toc.length > 40 && (
                <button type="button" onClick={() => setExpanded((value) => !value)} className="text-sm underline-offset-4 hover:underline">
                    {expanded ? 'Show fewer' : `Show all ${book.toc.length} entries`}
                </button>
            )}
        </Section>
    )
}

function SearchInBook({ book }: { book: BookDetail }) {
    const [query, setQuery] = useState('')
    const [response, setResponse] = useState<SearchResponse | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const onSubmit = async (event: FormEvent) => {
        event.preventDefault()
        if (!query.trim()) return
        setLoading(true)
        setError(null)
        try {
            setResponse(await searchRecords(query.trim(), book.id))
        } catch (reason) {
            setResponse(null)
            setError(reason instanceof Error ? reason.message : 'Search failed.')
        } finally {
            setLoading(false)
        }
    }

    return (
        <Section title="Search in this book" icon={<Search className="h-5 w-5" />}>
            <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row" role="search">
                <input
                    type="text"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={`Ask something answered in ${book.title}`}
                    aria-label={`Search in ${book.title}`}
                    className="h-12 w-full border-2 border-zinc-950 px-4 outline-none focus:bg-zinc-50"
                />
                <button
                    type="submit"
                    disabled={!query.trim() || loading}
                    className="inline-flex h-12 min-w-28 items-center justify-center gap-2 border-2 border-zinc-950 bg-zinc-950 px-4 font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-700"
                >
                    <Search className="h-4 w-4" />
                    Search
                </button>
            </form>
            {loading && <ResultSkeleton />}
            {!loading && error && <p className="border-2 border-zinc-950 p-4 text-sm">{error}</p>}
            {!loading && response && response.results.length === 0 && (
                <p className="border-2 border-zinc-950 p-4 text-sm">Nothing in this book answers that. Try the whole library from the Search tab.</p>
            )}
            {!loading && response && response.results.length > 0 && <ResultsView results={response.results} layout={response.layout} query={response.query} />}
        </Section>
    )
}

function PageBrowser({ book }: { book: BookDetail }) {
    const { openLightbox, togglePage } = useViewer()
    const [batches, setBatches] = useState<BookPages[]>([])
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const pages = useMemo(() => batches.flatMap((batch) => batch.pages), [batches])
    const total = book.page_count ?? 0

    const loadMore = async (start: number) => {
        setLoading(true)
        try {
            const batch = await getBookPages(book.id, start, PAGE_BATCH)
            setBatches((current) => [...current, batch])
            setError(null)
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Could not load pages.')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        const controller = new AbortController()
        getBookPages(book.id, 1, PAGE_BATCH, controller.signal)
            .then((batch) => setBatches([batch]))
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load pages.')
            })
        return () => controller.abort()
    }, [book.id])

    const gallery: LightboxItem[] = pages
        .filter((page) => page.image)
        .map((page) => ({ kind: 'image', src: page.image as string, title: `Page ${page.page}`, source: book.title }))

    return (
        <Section title="Browse pages" icon={<BookOpen className="h-5 w-5" />} note={total ? `${pages.length} of ${total}` : undefined}>
            {error && <p className="text-sm text-zinc-700">{error}</p>}
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 xl:grid-cols-8">
                {pages.map((page) => (
                    <figure key={page.page} className="flex flex-col border border-zinc-300 bg-white">
                        {page.image ? (
                            <button
                                type="button"
                                onClick={() => openLightbox(gallery, gallery.findIndex((item) => item.kind === 'image' && item.title === `Page ${page.page}`))}
                                className="block"
                                aria-label={`Enlarge page ${page.page}`}
                            >
                                <img src={page.image} alt={`Page ${page.page}`} loading="lazy" className="aspect-[3/4] w-full bg-zinc-100 object-cover object-top" />
                            </button>
                        ) : (
                            <div className="aspect-[3/4] w-full bg-zinc-100" />
                        )}
                        <figcaption className="flex items-center justify-between border-t border-zinc-200 px-1.5 py-0.5 text-xs text-zinc-600">
                            <span>p. {page.page}</span>
                            <button
                                type="button"
                                onClick={() => togglePage({ pdfLink: book.pdf_link, pageNumber: page.page, title: book.title })}
                                className="underline-offset-2 hover:underline"
                            >
                                PDF
                            </button>
                        </figcaption>
                    </figure>
                ))}
            </div>
            {pages.length < total && (
                <button
                    type="button"
                    disabled={loading}
                    onClick={() => loadMore(pages.length + 1)}
                    className="border-2 border-zinc-950 px-4 py-2 text-sm font-medium hover:bg-zinc-50 disabled:opacity-60"
                >
                    {loading ? 'Loading…' : `Show ${Math.min(PAGE_BATCH, total - pages.length)} more pages`}
                </button>
            )}
        </Section>
    )
}

/** One book: cover and details, contents, search inside it, key concepts, figures and its pages. */
export function BookPage({ bookId }: { bookId: number }) {
    const [book, setBook] = useState<BookDetail | null>(null)
    const [error, setError] = useState<string | null>(null)
    const { togglePage } = useViewer()
    const { user } = useAuth()

    // Shows up under "continue reading" on the scholar's dashboard.
    useEffect(() => {
        if (user) recordActivity('book', String(bookId), bookId)
    }, [bookId, user])

    useEffect(() => {
        const controller = new AbortController()
        getBook(bookId, controller.signal)
            .then((data) => {
                setBook(data)
                window.scrollTo({ top: 0 })
            })
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load the book.')
            })
        return () => controller.abort()
    }, [bookId])

    const gallery = useMemo(() => (book ? book.figures.map((figure) => figureLightboxItem(figure, `${book.title} · p. ${figure.page_number}`)) : []), [book])

    if (error) return <p className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!book) {
        return (
            <div className="grid animate-pulse gap-6 md:grid-cols-[14rem_1fr]">
                <div className="aspect-[3/4] bg-zinc-100" />
                <div className="space-y-3">
                    <div className="h-8 w-2/3 bg-zinc-200" />
                    <div className="h-4 w-1/3 bg-zinc-100" />
                </div>
            </div>
        )
    }

    const details = [book.author, book.department, book.publication_year, book.language && LANGUAGE_LABEL[book.language]].filter(Boolean)
    const citation = [book.publisher, book.edition, book.isbn && `ISBN ${book.isbn}`].filter(Boolean)
    return (
        <div className="space-y-8">
            <a href={libraryHref()} className="inline-flex items-center gap-1 text-sm text-zinc-600 underline-offset-4 hover:text-zinc-950 hover:underline">
                <ArrowLeft className="h-4 w-4" /> Library
            </a>
            <header className="grid gap-6 md:grid-cols-[14rem_minmax(0,1fr)]">
                <BookCover book={book} className="aspect-[3/4] w-40 border-2 border-zinc-950 md:w-full" />
                <div className="flex min-w-0 flex-col gap-4">
                    <div>
                        <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">Book</p>
                        <h1 className="text-2xl font-semibold tracking-tight md:text-4xl">{book.title}</h1>
                        {details.length > 0 && <p className="mt-1 text-zinc-600">{details.join(' · ')}</p>}
                        {citation.length > 0 && <p className="text-sm text-zinc-500">{citation.join(' · ')}</p>}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Stat value={book.page_count} label="pages" />
                        <Stat value={book.chunk_count} label="passages" />
                        <Stat value={book.figure_count} label="figures" />
                        <Stat value={book.table_count} label="tables" />
                        <Stat value={book.concept_count} label="concepts" />
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <a
                            href={readerHref(book.id, 1)}
                            className="inline-flex items-center gap-2 border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800"
                        >
                            <BookOpen className="h-4 w-4" /> Read
                        </a>
                        <button
                            type="button"
                            onClick={() => togglePage({ pdfLink: book.pdf_link, pageNumber: 1, title: book.title })}
                            className="inline-flex items-center gap-2 border-2 border-zinc-950 px-4 py-2 text-sm font-medium hover:bg-zinc-50"
                        >
                            Open the PDF
                        </button>
                        <a href={book.pdf_link} download className="inline-flex items-center gap-2 border-2 border-zinc-950 px-4 py-2 text-sm font-medium hover:bg-zinc-50">
                            <Download className="h-4 w-4" /> Download PDF
                        </a>
                    </div>
                </div>
            </header>

            <SearchInBook book={book} />

            {book.toc.length > 0 && <Contents book={book} />}

            {book.concepts.length > 0 && (
                <Section title="Key concepts" icon={<Network className="h-5 w-5" />} note="open in the Explorer">
                    <div className="flex flex-wrap gap-2">
                        {book.concepts.map((concept) => (
                            <a key={concept.id} href={exploreHref(concept.id)} className="border border-zinc-950 px-3 py-1.5 text-sm hover:bg-zinc-100">
                                {concept.label}
                                {concept.book_count > 1 && <span className="ml-1.5 text-xs text-zinc-500">in {concept.book_count} books</span>}
                            </a>
                        ))}
                    </div>
                </Section>
            )}

            {book.figures.length > 0 && (
                <Section title="Figures" note={`${book.figures.length} captioned · ${book.figure_count} in all`}>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                        {book.figures.map((figure, index) => (
                            <FigureView key={`${figure.image}-${index}`} figure={figure} source={`${book.title} · p. ${figure.page_number}`} gallery={gallery} index={index} />
                        ))}
                    </div>
                </Section>
            )}

            <PageBrowser book={book} />
        </div>
    )
}

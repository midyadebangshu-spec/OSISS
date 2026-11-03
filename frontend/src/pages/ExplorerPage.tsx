import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { Network, Search } from 'lucide-react'
import { ConceptView } from '../components/explorer/ConceptView'
import { getConcept, searchConcepts } from '../lib/conceptsApi'
import { exploreHref, navigate } from '../lib/route'
import type { ConceptDetail, ConceptSummary } from '../types/concepts'

function ConceptChip({ concept }: { concept: ConceptSummary }) {
    return (
        <a
            href={exploreHref(concept.id)}
            className="inline-flex items-baseline gap-1.5 border border-zinc-950 px-3 py-1.5 text-sm hover:bg-zinc-100"
        >
            {concept.label}
            <span className="text-xs text-zinc-500">{concept.book_count}</span>
        </a>
    )
}

/** Concept lookup with suggestions as you type (any language; romanized Bengali / Hindi too). */
function ConceptSearch({ autoFocus }: { autoFocus: boolean }) {
    const [query, setQuery] = useState('')
    const [suggestions, setSuggestions] = useState<ConceptSummary[]>([])
    const [open, setOpen] = useState(false)
    const [active, setActive] = useState(0)
    const [searched, setSearched] = useState(false)
    const input = useRef<HTMLInputElement>(null)

    useEffect(() => {
        if (autoFocus) input.current?.focus()
    }, [autoFocus])

    useEffect(() => {
        const text = query.trim()
        if (!text) return
        const controller = new AbortController()
        const timer = setTimeout(() => {
            searchConcepts(text, controller.signal, 8)
                .then((data) => {
                    setSuggestions(data.concepts)
                    setActive(0)
                    setSearched(true)
                })
                .catch(() => undefined)
        }, 180)
        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [query])

    const go = (concept: ConceptSummary | undefined) => {
        if (!concept) return
        setOpen(false)
        setQuery('')
        setSuggestions([])
        navigate(exploreHref(concept.id))
    }

    const onSubmit = (event: FormEvent) => {
        event.preventDefault()
        go(suggestions[active])
    }

    const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key === 'ArrowDown') {
            event.preventDefault()
            setActive((index) => Math.min(index + 1, suggestions.length - 1))
        } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => Math.max(index - 1, 0))
        } else if (event.key === 'Escape') {
            setOpen(false)
        }
    }

    const showList = open && query.trim().length > 0 && searched
    return (
        <form onSubmit={onSubmit} className="relative w-full" role="search">
            <label htmlFor="concept-search" className="sr-only">
                Find a concept
            </label>
            <div className="flex w-full gap-3">
                <input
                    ref={input}
                    id="concept-search"
                    type="text"
                    value={query}
                    autoComplete="off"
                    role="combobox"
                    aria-expanded={showList}
                    aria-controls="concept-suggestions"
                    aria-activedescendant={showList && suggestions[active] ? `concept-option-${suggestions[active].id}` : undefined}
                    onChange={(event) => {
                        setQuery(event.target.value)
                        setOpen(true)
                    }}
                    onFocus={() => setOpen(true)}
                    onBlur={() => setTimeout(() => setOpen(false), 150)}
                    onKeyDown={onKeyDown}
                    placeholder="Find a concept: stack, photosynthesis, প্রতিসরণ, संक्षारण…"
                    className="h-12 w-full rounded-none border-2 border-zinc-950 bg-white px-4 text-base outline-none focus:bg-zinc-50"
                />
                <button
                    type="submit"
                    disabled={!suggestions.length}
                    className="inline-flex h-12 min-w-28 items-center justify-center gap-2 border-2 border-zinc-950 bg-zinc-950 px-4 font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-700"
                >
                    <Search className="h-4 w-4" />
                    Explore
                </button>
            </div>
            {showList && (
                <ul id="concept-suggestions" role="listbox" className="absolute left-0 right-0 top-full z-20 mt-1 max-h-80 overflow-y-auto border-2 border-zinc-950 bg-white shadow-lg">
                    {suggestions.length === 0 && <li className="px-4 py-3 text-sm text-zinc-600">No concept by that name. Try another spelling, or search the books.</li>}
                    {suggestions.map((concept, index) => {
                        const others = concept.aliases.filter((alias) => alias.toLowerCase() !== concept.label.toLowerCase()).slice(0, 3)
                        return (
                            <li
                                key={concept.id}
                                id={`concept-option-${concept.id}`}
                                role="option"
                                aria-selected={index === active}
                                onMouseDown={(event) => {
                                    event.preventDefault()
                                    go(concept)
                                }}
                                onMouseEnter={() => setActive(index)}
                                className={`flex cursor-pointer items-baseline justify-between gap-3 px-4 py-2 ${index === active ? 'bg-zinc-100' : ''}`}
                            >
                                <span className="min-w-0">
                                    <span className="font-medium">{concept.label}</span>
                                    {others.length > 0 && <span className="ml-2 truncate text-sm text-zinc-500">{others.join(' · ')}</span>}
                                </span>
                                <span className="shrink-0 text-xs text-zinc-500">
                                    {concept.book_count} book{concept.book_count === 1 ? '' : 's'}
                                </span>
                            </li>
                        )
                    })}
                </ul>
            )}
        </form>
    )
}

type ExplorerPageProps = { conceptId: number | null; active: boolean }

export function ExplorerPage({ conceptId, active }: ExplorerPageProps) {
    const [browse, setBrowse] = useState<ConceptSummary[] | null>(null)
    const [built, setBuilt] = useState(true)
    const [detail, setDetail] = useState<ConceptDetail | null>(null)
    const [loadedId, setLoadedId] = useState<number | null>(null)
    const [error, setError] = useState<string | null>(null)

    // Browse list: loaded the first time the tab is opened.
    useEffect(() => {
        if (!active || browse !== null) return
        const controller = new AbortController()
        searchConcepts('', controller.signal, 48)
            .then((data) => {
                setBuilt(data.built)
                setBrowse(data.concepts)
            })
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Concept graph unavailable.')
            })
        return () => controller.abort()
    }, [active, browse])

    useEffect(() => {
        if (conceptId === null) return
        const controller = new AbortController()
        getConcept(conceptId, controller.signal)
            .then((data) => {
                setDetail(data)
                setLoadedId(conceptId)
                setError(null)
                window.scrollTo({ top: 0 })
            })
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) {
                    setLoadedId(conceptId)
                    setDetail(null)
                    setError(reason instanceof Error ? reason.message : 'Could not load the concept.')
                }
            })
        return () => controller.abort()
    }, [conceptId])

    const loading = conceptId !== null && loadedId !== conceptId
    const showing = conceptId !== null && !loading ? detail : null

    return (
        <div className="space-y-6">
            <div className="sticky top-0 z-10 border-b-2 border-zinc-950 bg-white pb-4 pt-2">
                <ConceptSearch autoFocus={active && conceptId === null} />
            </div>

            {conceptId === null && (
                <section className="space-y-5">
                    <div className="max-w-3xl space-y-2">
                        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight md:text-3xl">
                            <Network className="h-7 w-7" />
                            Context Explorer
                        </h1>
                        <p className="text-zinc-700">
                            See how a concept is defined in each book, which concepts the books connect it to (and the sentences that do), its figures, and where
                            it appears. Names are linked across English, বাংলা and हिंदी where the books themselves print the English term.
                        </p>
                    </div>
                    {!built && (
                        <p className="border-2 border-zinc-950 p-4 text-sm">
                            The concept graph has not been built yet. Run <code className="bg-zinc-100 px-1">src/build_concepts.py</code> on the server (or
                            "Rebuild the concept graph" in the admin app).
                        </p>
                    )}
                    {error && <p className="border-2 border-zinc-950 p-4 text-sm">{error}</p>}
                    {browse && browse.length > 0 && (
                        <div className="space-y-3">
                            <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">Defined across several books</p>
                            <div className="flex flex-wrap gap-2">
                                {browse.map((concept) => (
                                    <ConceptChip key={concept.id} concept={concept} />
                                ))}
                            </div>
                        </div>
                    )}
                </section>
            )}

            {loading && (
                <div className="animate-pulse space-y-4">
                    <div className="h-10 w-1/3 bg-zinc-200" />
                    <div className="h-80 bg-zinc-100" />
                </div>
            )}
            {conceptId !== null && !loading && error && <p className="border-2 border-zinc-950 p-4 text-sm">{error}</p>}
            {showing && <ConceptView key={showing.id} concept={showing} />}
        </div>
    )
}

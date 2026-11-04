import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Layers, PauseCircle, PlayCircle, Trash2 } from 'lucide-react'
import { pageLabel } from '../../lib/results'
import { readerHref } from '../../lib/route'
import {
    createCardsBulk, deleteCard, getDueCards, listCards, listCollections, listProjects, reviewCard, updateCard,
} from '../../lib/workspaceApi'
import type { Card, CardGrade, Collection, Project, StudyStats } from '../../types/workspace'

const FIELD = 'border-2 border-zinc-950 bg-white px-2 py-1.5 text-sm outline-none focus:bg-zinc-50'
const PRIMARY = 'inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white disabled:opacity-40'

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

function days(count: number): string {
    return count === 1 ? '1 day' : `${count} days`
}

const GRADES: { grade: CardGrade; key: 'again' | 'hard' | 'good' | 'easy'; label: string }[] = [
    { grade: 0, key: 'again', label: 'Again' },
    { grade: 1, key: 'hard', label: 'Hard' },
    { grade: 2, key: 'good', label: 'Good' },
    { grade: 3, key: 'easy', label: 'Easy' },
]

function Answer({ card }: { card: Card }) {
    const item = card.evidence
    return (
        <div className="space-y-2 border-t-2 border-zinc-950 pt-3">
            <blockquote className="whitespace-pre-line leading-relaxed">“{item.quote}”</blockquote>
            <p className="text-sm text-zinc-600">
                {item.book_title}{item.book_author ? ` · ${item.book_author}` : ''}
                {item.page_number ? ` · p. ${pageLabel(item.page_number, item.printed_page)}` : ''}
                {item.book_id && item.page_number && (
                    <> · <a href={readerHref(item.book_id, item.page_number)} target="_blank" rel="noreferrer" className="underline underline-offset-4">Open the page</a></>
                )}
            </p>
        </div>
    )
}

/** A review session over the cards due now: question, answer (the cited quote), then how well it was recalled. */
function ReviewSession({ projectId, onDone }: { projectId?: number; onDone: () => void }) {
    const [queue, setQueue] = useState<Card[] | null>(null)
    const [shown, setShown] = useState(false)
    const [done, setDone] = useState(0)
    const [stats, setStats] = useState<StudyStats | null>(null)
    const [error, setError] = useState<string | null>(null)
    const busy = useRef(false)

    useEffect(() => {
        const controller = new AbortController()
        getDueCards(projectId, controller.signal)
            .then((result) => {
                setQueue(result.cards)
                setStats(result.stats)
            })
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [projectId])

    const current = queue?.[0]

    const answer = useCallback(
        async (grade: CardGrade) => {
            if (!current || busy.current) return
            busy.current = true
            try {
                const result = await reviewCard(current.id, grade)
                setStats(result.stats)
                setDone((count) => count + 1)
                setShown(false)
                // A card answered "again" comes back at the end of this session; the others leave it.
                setQueue((cards) => (cards ? [...cards.slice(1), ...(grade === 0 ? [result.card] : [])] : cards))
            } catch (failure) {
                setError(message(failure))
            } finally {
                busy.current = false
            }
        },
        [current],
    )

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if ((event.target as HTMLElement).closest('input, textarea, select')) return
            if (!shown && (event.key === ' ' || event.key === 'Enter')) {
                event.preventDefault()
                setShown(true)
            } else if (shown && ['1', '2', '3', '4'].includes(event.key)) {
                void answer((Number(event.key) - 1) as CardGrade)
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [shown, answer])

    if (error) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm text-red-700">{error}</p>
    if (!queue) return <p className="text-sm text-zinc-500">Loading…</p>
    if (!current) {
        return (
            <div className="space-y-3 border-2 border-zinc-950 p-6 text-center">
                <Check className="mx-auto h-6 w-6" />
                <p className="font-semibold">{done > 0 ? `Done: ${done} ${done === 1 ? 'review' : 'reviews'} this session.` : 'Nothing is due.'}</p>
                {stats && <p className="text-sm text-zinc-600">{stats.total} cards · {stats.reviewed_today} reviewed today{stats.retention_30_days !== null ? ` · ${Math.round(stats.retention_30_days * 100)}% recalled over 30 days` : ''}</p>}
                <button type="button" onClick={onDone} className={PRIMARY}>Back to your cards</button>
            </div>
        )
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between text-sm text-zinc-600">
                <button type="button" onClick={onDone} className="underline-offset-4 hover:underline">End session</button>
                <span aria-live="polite">{queue.length} left · {done} done</span>
            </div>
            <article className="space-y-4 border-2 border-zinc-950 bg-white p-5">
                <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">{current.is_new ? 'New card' : `Interval ${days(current.interval_days)}`}</p>
                <p className="text-xl font-semibold leading-snug">{current.front}</p>
                {shown ? <Answer card={current} /> : (
                    <button type="button" onClick={() => setShown(true)} className={PRIMARY}>Show the answer <span className="text-xs opacity-70">(space)</span></button>
                )}
            </article>
            {shown && (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="How well did you recall it?">
                    {GRADES.map((item) => (
                        <button key={item.grade} type="button" onClick={() => void answer(item.grade)} className="border-2 border-zinc-950 px-2 py-2 text-sm hover:bg-zinc-100">
                            <span className="block font-medium">{item.label} <span className="text-xs text-zinc-500">({item.grade + 1})</span></span>
                            <span className="block text-xs text-zinc-500">{days(current.next_intervals[item.key])}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}

function CardRow({ card, onChange }: { card: Card; onChange: () => void }) {
    const [front, setFront] = useState(card.front)
    const [error, setError] = useState<string | null>(null)
    const act = (work: () => Promise<unknown>) => work().then(() => { setError(null); onChange() }).catch((failure: unknown) => setError(message(failure)))
    return (
        <li className={`space-y-1 border-2 p-3 text-sm ${card.suspended ? 'border-zinc-300 text-zinc-500' : 'border-zinc-950'}`}>
            <input className={`${FIELD} w-full font-medium`} value={front} maxLength={500} onChange={(event) => setFront(event.target.value)} onBlur={() => front.trim() && front !== card.front && void act(() => updateCard(card.id, { front }))} aria-label="Question on the card" />
            <p className="line-clamp-2 text-zinc-600">“{card.evidence.quote}”</p>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
                <span>{card.evidence.book_title}{card.evidence.page_number ? ` · p. ${pageLabel(card.evidence.page_number, card.evidence.printed_page)}` : ''}</span>
                <span>{card.is_new ? 'new' : `due ${card.due} · every ${days(card.interval_days)}`}{card.lapses > 0 ? ` · ${card.lapses} lapses` : ''}</span>
                <button type="button" onClick={() => void act(() => updateCard(card.id, { suspended: !card.suspended }))} className="inline-flex items-center gap-1 underline-offset-4 hover:underline">
                    {card.suspended ? <><PlayCircle className="h-3.5 w-3.5" /> Resume</> : <><PauseCircle className="h-3.5 w-3.5" /> Pause</>}
                </button>
                <button type="button" onClick={() => void act(() => deleteCard(card.id))} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-3.5 w-3.5" /> Delete</button>
            </p>
            {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
        </li>
    )
}

export function StudyView() {
    const [projectId, setProjectId] = useState<number | undefined>(undefined)
    const [projects, setProjects] = useState<Project[]>([])
    const [collections, setCollections] = useState<Collection[]>([])
    const [cards, setCards] = useState<Card[] | null>(null)
    const [stats, setStats] = useState<StudyStats | null>(null)
    const [reviewing, setReviewing] = useState(false)
    const [source, setSource] = useState('')
    const [notice, setNotice] = useState<string | null>(null)
    const [error, setError] = useState<string | null>(null)

    const load = useCallback(
        (signal?: AbortSignal) =>
            Promise.all([listCards(projectId, signal), getDueCards(projectId, signal)])
                .then(([list, due]) => {
                    setCards(list.cards)
                    setStats(due.stats)
                })
                .catch((failure: unknown) => {
                    if (!signal?.aborted) setError(message(failure))
                }),
        [projectId],
    )

    useEffect(() => {
        const controller = new AbortController()
        void load(controller.signal)
        return () => controller.abort()
    }, [load, reviewing])

    useEffect(() => {
        listProjects().then(setProjects).catch(() => undefined)
        listCollections().then(setCollections).catch(() => undefined)
    }, [])

    if (reviewing) return <ReviewSession projectId={projectId} onDone={() => setReviewing(false)} />

    const makeCards = async () => {
        const [kind, rawId] = source.split(':')
        if (!kind || !rawId) return
        try {
            const result = await createCardsBulk(kind === 'c' ? { collection_id: Number(rawId) } : { project_id: Number(rawId) })
            setNotice(`${result.created} new ${result.created === 1 ? 'card' : 'cards'}${result.existing ? `, ${result.existing} already existed` : ''}${result.skipped ? `, ${result.skipped} items need a question you write yourself (use “Flashcard” on the item)` : ''}.`)
            await load()
        } catch (failure) {
            setError(message(failure))
        }
    }

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-4">
                <div className="space-y-0.5">
                    <p className="text-2xl font-semibold tabular-nums">{stats?.due ?? '…'} <span className="text-base font-normal text-zinc-600">due now</span></p>
                    {stats && <p className="text-xs text-zinc-500">{stats.total} cards · {stats.new} new · {stats.mature} well known{stats.retention_30_days !== null ? ` · ${Math.round(stats.retention_30_days * 100)}% recalled (30 days)` : ''}</p>}
                </div>
                <button type="button" disabled={!stats || stats.due === 0} onClick={() => setReviewing(true)} className={PRIMARY}><Layers className="h-4 w-4" /> Start review</button>
                <label className="ml-auto flex items-center gap-2 text-sm">Project
                    <select className={FIELD} value={projectId ?? ''} onChange={(event) => setProjectId(event.target.value ? Number(event.target.value) : undefined)}>
                        <option value="">All cards</option>
                        {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
                    </select>
                </label>
            </div>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {notice && <p role="status" className="text-sm text-zinc-700">{notice}</p>}

            <section className="space-y-2 border border-zinc-300 p-3 text-sm">
                <p className="font-medium">Make cards from definitions</p>
                <div className="flex flex-wrap gap-2">
                    <select className={FIELD} value={source} onChange={(event) => setSource(event.target.value)} aria-label="Collection or project to make cards from">
                        <option value="">Choose a collection or project…</option>
                        {collections.map((collection) => <option key={`c${collection.id}`} value={`c:${collection.id}`}>Collection: {collection.name}</option>)}
                        {projects.map((project) => <option key={`p${project.id}`} value={`p:${project.id}`}>Project: {project.title}</option>)}
                    </select>
                    <button type="button" disabled={!source} onClick={() => void makeCards()} className="border-2 border-zinc-950 px-3 py-1.5 disabled:opacity-40">Make cards</button>
                </div>
                <p className="text-xs text-zinc-500">A card asks “What is meant by …?” for each saved definition and answers with the book’s own words and page. For any other saved item, use “Flashcard” on it and write the question. Cards come back after 1 day, then 6, then longer (SM-2).</p>
            </section>

            {cards === null ? <p className="text-sm text-zinc-500">Loading…</p> : cards.length === 0 ? (
                <p className="text-sm text-zinc-600">No cards yet.</p>
            ) : (
                <ul className="space-y-2">{cards.map((card) => <CardRow key={`${card.id}-${card.front}-${card.suspended}`} card={card} onChange={() => void load()} />)}</ul>
            )}
        </div>
    )
}

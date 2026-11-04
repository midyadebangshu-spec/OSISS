import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, PointerEvent as ReactPointerEvent } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Copy, Highlighter, Trash2, X } from 'lucide-react'
import { SaveButton } from '../components/SaveButton'
import { useAuth } from '../lib/auth'
import { findPage, getReaderPage } from '../lib/libraryApi'
import { libraryHref, readerHref } from '../lib/route'
import { insideRect, lineRects, orderedRange, selectedText, wordIndexAt } from '../lib/selection'
import type { Rect } from '../lib/selection'
import { deleteEvidence, listHighlights, recordActivity, saveEvidence } from '../lib/workspaceApi'
import type { ReaderPage as ReaderPageData } from '../types/library'
import type { Evidence } from '../types/workspace'

type Drag = { anchor: number; focus: number; dragging: boolean }

/** The book one page at a time, mounted with a key per page so each page starts clean: select words to highlight, copy or save them; your highlights are drawn on the page. */
export function ReaderPage({ bookId, page }: { bookId: number; page: number }) {
    const { user } = useAuth()
    const [data, setData] = useState<ReaderPageData | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [loadedHighlights, setHighlights] = useState<Evidence[]>([])
    const [selection, setSelection] = useState<Drag | null>(null)
    const [chosen, setChosen] = useState<Evidence | null>(null)
    const [jump, setJump] = useState('')
    const [notice, setNotice] = useState<string | null>(null)
    const surface = useRef<HTMLDivElement>(null)
    const highlights = user ? loadedHighlights : []

    useEffect(() => {
        const controller = new AbortController()
        getReaderPage(bookId, page, controller.signal)
            .then((result) => {
                setData(result)
                window.scrollTo({ top: 0 })
            })
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load the page.')
            })
        return () => controller.abort()
    }, [bookId, page])

    const loadHighlights = useCallback(
        (signal?: AbortSignal) => {
            if (!user) return
            listHighlights(bookId, page, signal)
                .then(setHighlights)
                .catch(() => undefined)
        },
        [bookId, page, user],
    )

    useEffect(() => {
        const controller = new AbortController()
        loadHighlights(controller.signal)
        return () => controller.abort()
    }, [loadHighlights])

    // Where the scholar stopped, for "continue reading" (after a short pause, so paging quickly sends one request).
    useEffect(() => {
        if (!user) return
        const timer = window.setTimeout(() => recordActivity('book', String(bookId), bookId, page), 800)
        return () => window.clearTimeout(timer)
    }, [bookId, page, user])

    const go = useCallback(
        (target: number | null) => {
            if (target) window.location.hash = readerHref(bookId, target)
        },
        [bookId],
    )

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            const target = event.target as HTMLElement
            if (target.closest('input, textarea, [role="dialog"]')) return
            if (event.key === 'ArrowRight') go(data?.next ?? null)
            if (event.key === 'ArrowLeft') go(data?.previous ?? null)
            if (event.key === 'Escape') {
                setSelection(null)
                setChosen(null)
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [data, go])

    const pointFor = (event: ReactPointerEvent): [number, number] => {
        const box = surface.current?.getBoundingClientRect()
        if (!box || !box.width) return [0, 0]
        return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height]
    }

    const onPointerDown = (event: ReactPointerEvent) => {
        if (!data || event.button !== 0) return
        const [x, y] = pointFor(event)
        const hit = highlights.find((item) => ((item.meta.rects as Rect[] | undefined) ?? []).some((rect) => insideRect(rect, x, y)))
        if (hit) {
            setChosen(hit)
            setSelection(null)
            return
        }
        setChosen(null)
        const index = wordIndexAt(data.words, x, y, data.height / data.width)
        if (index < 0) return
        event.currentTarget.setPointerCapture(event.pointerId)
        setSelection({ anchor: index, focus: index, dragging: true })
    }

    const onPointerMove = (event: ReactPointerEvent) => {
        if (!data || !selection?.dragging) return
        const [x, y] = pointFor(event)
        const index = wordIndexAt(data.words, x, y, data.height / data.width)
        if (index >= 0 && index !== selection.focus) setSelection({ ...selection, focus: index })
    }

    const onPointerUp = () => setSelection((current) => (current ? { ...current, dragging: false } : current))

    const range = data && selection ? orderedRange(selection.anchor, selection.focus) : null
    const selectedRects = data && range ? lineRects(data.words, range[0], range[1]) : []
    const text = data && range ? selectedText(data.words, range[0], range[1]) : ''
    const draft = data && range && text ? { kind: 'highlight' as const, book_id: bookId, page_number: page, quote: text, meta: { rects: selectedRects } } : null

    const highlight = async () => {
        if (!draft) return
        try {
            await saveEvidence(draft)
            setSelection(null)
            loadHighlights()
        } catch (failure) {
            setNotice(failure instanceof Error ? failure.message : 'Could not save the highlight.')
        }
    }

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text)
            setNotice('Copied.')
        } catch {
            setNotice('Copying is not allowed in this browser.')
        }
    }

    const removeHighlight = async (item: Evidence) => {
        try {
            await deleteEvidence(item.id)
            setChosen(null)
            loadHighlights()
        } catch (failure) {
            setNotice(failure instanceof Error ? failure.message : 'Could not remove the highlight.')
        }
    }

    const goToLabel = async (event: FormEvent) => {
        event.preventDefault()
        if (!jump.trim()) return
        try {
            go(await findPage(bookId, jump.trim()))
            setJump('')
        } catch {
            setNotice(`No page numbered “${jump.trim()}”.`)
        }
    }

    if (error) {
        return (
            <div className="space-y-3">
                <a href={libraryHref(bookId)} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"><ArrowLeft className="h-4 w-4" /> Back to the book</a>
                <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
            </div>
        )
    }
    if (!data) return <p className="py-8 text-sm text-zinc-500">Loading the page…</p>

    return (
        <div className="mx-auto max-w-4xl space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                <a href={libraryHref(bookId)} className="inline-flex min-w-0 items-center gap-1 underline-offset-4 hover:underline">
                    <ArrowLeft className="h-4 w-4 shrink-0" /> <span className="truncate">{data.title}</span>
                </a>
                <div className="flex items-center gap-2">
                    <button type="button" disabled={!data.previous} onClick={() => go(data.previous)} aria-label="Previous page" className="border-2 border-zinc-950 p-1.5 disabled:opacity-30"><ChevronLeft className="h-4 w-4" /></button>
                    <span aria-live="polite" className="min-w-36 text-center">
                        {data.printed_label ? `Page ${data.printed_label}` : `Page ${data.page}`}
                        <span className="text-zinc-500"> · PDF {data.page}/{data.page_count}</span>
                    </span>
                    <button type="button" disabled={!data.next} onClick={() => go(data.next)} aria-label="Next page" className="border-2 border-zinc-950 p-1.5 disabled:opacity-30"><ChevronRight className="h-4 w-4" /></button>
                </div>
                <form onSubmit={(event) => void goToLabel(event)} className="flex items-center gap-2">
                    <input value={jump} onChange={(event) => setJump(event.target.value)} placeholder="Go to page" aria-label="Go to printed page number" className="w-28 border-2 border-zinc-950 px-2 py-1 outline-none focus:bg-zinc-50" />
                </form>
            </div>

            <div
                ref={surface}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                className="relative mx-auto w-full cursor-text select-none border-2 border-zinc-950 bg-white"
                style={{ touchAction: 'pan-y pinch-zoom', aspectRatio: `${data.width} / ${data.height}` }}
            >
                <img src={data.image} alt={`Page ${data.page} of ${data.title}`} draggable={false} className="absolute inset-0 h-full w-full" />
                {highlights.flatMap((item) =>
                    ((item.meta.rects as Rect[] | undefined) ?? []).map((rect, index) => (
                        <span
                            key={`${item.id}-${index}`}
                            className={`absolute bg-amber-300/50 mix-blend-multiply ${chosen?.id === item.id ? 'outline outline-2 outline-zinc-950' : ''}`}
                            style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }}
                        />
                    )),
                )}
                {selectedRects.map((rect, index) => (
                    <span
                        key={index}
                        className="absolute bg-sky-400/40 mix-blend-multiply"
                        style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${(rect[2] - rect[0]) * 100}%`, height: `${(rect[3] - rect[1]) * 100}%` }}
                    />
                ))}
            </div>
            {data.words.length === 0 && <p className="text-sm text-zinc-600">This page has no readable text to select (an image-only page).</p>}
            {data.ocr && data.words.length > 0 && <p className="text-xs text-zinc-500">Text on this page was recognised from a scan, so a selection may contain small errors.</p>}

            {(draft || chosen || notice) && (
                <div role="toolbar" aria-label="Selection" className="sticky bottom-3 z-10 flex flex-wrap items-center gap-4 border-2 border-zinc-950 bg-white p-3 text-sm shadow-lg">
                    {draft && (
                        <>
                            <span className="max-w-md truncate text-zinc-600" title={text}>“{text}”</span>
                            {user ? (
                                <>
                                    <button type="button" onClick={() => void highlight()} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Highlighter className="h-4 w-4" /> Highlight</button>
                                    <SaveButton draft={draft} label="Highlight and file…" onSaved={() => loadHighlights()} />
                                </>
                            ) : (
                                <a href="#/account" className="underline underline-offset-4">Sign in to highlight and save</a>
                            )}
                            <button type="button" onClick={() => void copy()} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Copy className="h-4 w-4" /> Copy</button>
                            <button type="button" onClick={() => setSelection(null)} aria-label="Clear selection" className="ml-auto"><X className="h-4 w-4" /></button>
                        </>
                    )}
                    {!draft && chosen && (
                        <>
                            <span className="max-w-md truncate text-zinc-600" title={chosen.quote}>Your highlight: “{chosen.quote}”</span>
                            <button type="button" onClick={() => void removeHighlight(chosen)} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Remove highlight</button>
                            <button type="button" onClick={() => setChosen(null)} aria-label="Close" className="ml-auto"><X className="h-4 w-4" /></button>
                        </>
                    )}
                    {notice && <span role="status" className="text-zinc-600">{notice}</span>}
                </div>
            )}
            {!user && <p className="text-xs text-zinc-500"><a href="#/account" className="underline underline-offset-4">Sign in</a> to keep highlights and pick up where you stopped.</p>}
        </div>
    )
}

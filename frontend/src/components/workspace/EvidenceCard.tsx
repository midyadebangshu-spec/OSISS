import { useState } from 'react'
import { Layers, Trash2 } from 'lucide-react'
import { createCard } from '../../lib/workspaceApi'
import { libraryHref, readerHref } from '../../lib/route'
import { pageLabel } from '../../lib/results'
import type { Evidence } from '../../types/workspace'

const CARD_KINDS = ['passage', 'definition', 'formula', 'highlight']

/** "Flashcard": a definition that carries its term asks "What is meant by …?"; anything else asks for the question first. */
function FlashcardAction({ item }: { item: Evidence }) {
    const [asking, setAsking] = useState(false)
    const [front, setFront] = useState('')
    const [status, setStatus] = useState<string | null>(null)
    const automatic = item.kind === 'definition' && typeof item.meta.term === 'string' && item.meta.term.trim() !== ''

    const make = async (question?: string) => {
        try {
            const card = await createCard(item.id, question)
            setStatus(`Card made: “${card.front}”`)
            setAsking(false)
        } catch (failure) {
            setStatus(failure instanceof Error ? failure.message : 'Could not make the card.')
        }
    }

    return (
        <div className="text-xs">
            <button type="button" onClick={() => (automatic ? void make() : setAsking((on) => !on))} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Layers className="h-3.5 w-3.5" /> Flashcard</button>
            {asking && (
                <form onSubmit={(event) => { event.preventDefault(); if (front.trim()) void make(front) }} className="mt-1 flex gap-2">
                    <input value={front} onChange={(event) => setFront(event.target.value)} maxLength={500} placeholder="The question this answers" aria-label="Question for the card" className="min-w-0 flex-1 border border-zinc-950 px-2 py-1" autoFocus />
                    <button type="submit" disabled={!front.trim()} className="border border-zinc-950 px-2 disabled:opacity-40">Make</button>
                </form>
            )}
            {status && <p role="status" className="mt-1 text-zinc-600">{status}</p>}
        </div>
    )
}

/** One saved item with its source. `onRemove` is the action offered (remove from collection, or delete). */
export function EvidenceCard({ item, onRemove, removeLabel = 'Delete' }: { item: Evidence; onRemove?: () => void; removeLabel?: string }) {
    return (
        <article className="space-y-2 border-2 border-zinc-950 bg-white p-3 text-sm">
            <div className="flex items-center justify-between gap-2 text-xs uppercase tracking-[0.16em] text-zinc-500">
                <span className="border border-zinc-950 px-2 py-0.5 text-zinc-950">{item.kind}</span>
                {onRemove && (
                    <button type="button" onClick={onRemove} className="inline-flex items-center gap-1 normal-case tracking-normal hover:text-red-700" aria-label={`${removeLabel}: ${item.quote.slice(0, 40) || item.concept_key || item.kind}`}>
                        <Trash2 className="h-3.5 w-3.5" /> {removeLabel}
                    </button>
                )}
            </div>
            {item.quote && <blockquote className="whitespace-pre-line leading-relaxed">“{item.quote}”</blockquote>}
            {item.concept_key && !item.quote && (
                <p className="font-medium">
                    <a href="#/explore" className="underline underline-offset-4">{item.concept_key}</a>
                </p>
            )}
            {item.quote && CARD_KINDS.includes(item.kind) && <FlashcardAction item={item} />}
            {item.book_title && (
                <p className="text-zinc-600">
                    {item.book_id ? (
                        <a href={libraryHref(item.book_id)} className="font-medium text-zinc-950 underline-offset-4 hover:underline">{item.book_title}</a>
                    ) : (
                        <span className="font-medium text-zinc-950">{item.book_title}</span>
                    )}
                    {item.book_author && <span> · {item.book_author}</span>}
                    {item.page_number && item.book_id && (
                        <span>
                            {' · '}
                            <a href={readerHref(item.book_id, item.page_number)} className="underline-offset-4 hover:underline">
                                Page {pageLabel(item.page_number, item.printed_page)}
                            </a>
                        </span>
                    )}
                </p>
            )}
        </article>
    )
}


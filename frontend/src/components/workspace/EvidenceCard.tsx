import { Trash2 } from 'lucide-react'
import { libraryHref } from '../../lib/route'
import type { Evidence } from '../../types/workspace'

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
            {item.book_title && (
                <p className="text-zinc-600">
                    {item.book_id ? (
                        <a href={libraryHref(item.book_id)} className="font-medium text-zinc-950 underline-offset-4 hover:underline">{item.book_title}</a>
                    ) : (
                        <span className="font-medium text-zinc-950">{item.book_title}</span>
                    )}
                    {item.book_author && <span> · {item.book_author}</span>}
                    {item.page_number && <span> · Page {item.page_number}</span>}
                </p>
            )}
        </article>
    )
}


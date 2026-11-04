import { useEffect, useState } from 'react'
import { BookOpen, Clock, FolderOpen, NotebookPen, Search } from 'lucide-react'
import { getDashboard } from '../../lib/workspaceApi'
import { libraryHref, workspaceHref } from '../../lib/route'
import type { Dashboard } from '../../types/workspace'
import { EvidenceCard } from './EvidenceCard'

function Panel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="space-y-3 border-2 border-zinc-950 p-4">
            <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">{icon}{title}</h2>
            {children}
        </section>
    )
}

const EMPTY = <p className="text-sm text-zinc-500">Nothing yet.</p>

export function DashboardView() {
    const [data, setData] = useState<Dashboard | null>(null)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        getDashboard(controller.signal)
            .then(setData)
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load the dashboard.')
            })
        return () => controller.abort()
    }, [])

    if (error) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!data) return <p className="text-sm text-zinc-500">Loading…</p>

    return (
        <div className="space-y-4">
            <p className="text-sm text-zinc-600">
                {data.counts.evidence} saved · {data.counts.collections} collections · {data.counts.notes} notes
                {data.new_books_30_days > 0 && <> · <a href={libraryHref()} className="underline underline-offset-4">{data.new_books_30_days} new {data.new_books_30_days === 1 ? 'book' : 'books'} this month</a></>}
            </p>
            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Continue reading" icon={<BookOpen className="h-4 w-4" />}>
                    {data.continue_reading.length === 0 ? EMPTY : (
                        <ul className="space-y-2 text-sm">
                            {data.continue_reading.map((book) => (
                                <li key={book.book_id}>
                                    <a href={libraryHref(book.book_id)} className="font-medium underline-offset-4 hover:underline">{book.title}</a>
                                    {book.author && <span className="text-zinc-600"> · {book.author}</span>}
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
                <Panel title="Recent searches" icon={<Search className="h-4 w-4" />}>
                    {data.recent_searches.length === 0 ? EMPTY : (
                        <ul className="space-y-1 text-sm">
                            {data.recent_searches.map((search) => (
                                <li key={search.query} className="flex items-center gap-2"><Clock className="h-3.5 w-3.5 shrink-0 text-zinc-400" /><span className="truncate">{search.query}</span></li>
                            ))}
                        </ul>
                    )}
                </Panel>
                <Panel title="Collections" icon={<FolderOpen className="h-4 w-4" />}>
                    {data.collections.length === 0 ? EMPTY : (
                        <ul className="space-y-1 text-sm">
                            {data.collections.map((collection) => (
                                <li key={collection.id}><a href={workspaceHref('collections', collection.id)} className="underline-offset-4 hover:underline">{collection.name}</a> <span className="text-zinc-500">({collection.item_count})</span></li>
                            ))}
                        </ul>
                    )}
                </Panel>
                <Panel title="Recent notes" icon={<NotebookPen className="h-4 w-4" />}>
                    {data.recent_notes.length === 0 ? EMPTY : (
                        <ul className="space-y-1 text-sm">
                            {data.recent_notes.map((note) => (
                                <li key={note.id}><a href={workspaceHref('notes', note.id)} className="underline-offset-4 hover:underline">{note.title || note.body.slice(0, 50)}</a></li>
                            ))}
                        </ul>
                    )}
                </Panel>
            </div>
            {data.recent_evidence.length > 0 && (
                <section className="space-y-3">
                    <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">Recently saved</h2>
                    <div className="grid gap-3 lg:grid-cols-2">{data.recent_evidence.map((item) => <EvidenceCard key={item.id} item={item} />)}</div>
                </section>
            )}
        </div>
    )
}

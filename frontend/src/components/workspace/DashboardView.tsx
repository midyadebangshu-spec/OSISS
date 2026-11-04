import { useEffect, useState } from 'react'
import { Bell, BookOpen, Clock, FolderKanban, FolderOpen, Layers, NotebookPen, Search, X } from 'lucide-react'
import { checkSavedSearches, deleteSavedSearch, getDashboard, markNotificationsRead } from '../../lib/workspaceApi'
import { readerHref, workspaceHref } from '../../lib/route'
import { libraryHref } from '../../lib/route'
import type { AppNotification, Dashboard } from '../../types/workspace'
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
    const [alerts, setAlerts] = useState<AppNotification[]>([])
    const [savedSearches, setSavedSearches] = useState<Dashboard['saved_searches']>([])

    useEffect(() => {
        const controller = new AbortController()
        getDashboard(controller.signal)
            .then((result) => {
                setData(result)
                setAlerts(result.unread_notifications)
                setSavedSearches(result.saved_searches)
                // Re-run saved searches the library has changed under; new notifications appear without a reload.
                if (result.saved_searches.length > 0) {
                    checkSavedSearches()
                        .then((checked) => {
                            if (!controller.signal.aborted) setAlerts(checked.notifications)
                        })
                        .catch(() => undefined)
                }
            })
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load the dashboard.')
            })
        return () => controller.abort()
    }, [])

    if (error) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!data) return <p className="text-sm text-zinc-500">Loading…</p>

    const dismiss = (id?: number) => {
        setAlerts((all) => (id ? all.filter((item) => item.id !== id) : []))
        markNotificationsRead(id).catch(() => undefined)
    }

    return (
        <div className="space-y-4">
            {alerts.length > 0 && (
                <section aria-label="Alerts" className="space-y-2 border-2 border-sky-700 bg-sky-50 p-3 text-sm">
                    <p className="flex items-center justify-between font-semibold"><span className="inline-flex items-center gap-2"><Bell className="h-4 w-4" /> {alerts.length} new {alerts.length === 1 ? 'answer' : 'answers'} to your saved searches</span>
                        <button type="button" onClick={() => dismiss()} className="text-xs font-normal underline-offset-4 hover:underline">Dismiss all</button></p>
                    <ul className="space-y-1">
                        {alerts.map((item) => (
                            <li key={item.id} className="flex items-start justify-between gap-2">
                                <span>{item.data.book_id ? <a href={item.data.page_number ? readerHref(item.data.book_id, item.data.page_number) : libraryHref(item.data.book_id)} onClick={() => dismiss(item.id)} className="underline underline-offset-4">{item.message}</a> : item.message}</span>
                                <button type="button" aria-label="Dismiss" onClick={() => dismiss(item.id)}><X className="h-4 w-4" /></button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
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
                                    <a href={book.page_number ? readerHref(book.book_id, book.page_number) : libraryHref(book.book_id)} className="font-medium underline-offset-4 hover:underline">{book.title}</a>
                                    {book.page_number && <span className="text-zinc-600"> · resume at PDF page {book.page_number}</span>}
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
                <Panel title="Study" icon={<Layers className="h-4 w-4" />}>
                    {data.study.total === 0 ? <p className="text-sm text-zinc-500">No flashcards yet. Use “Flashcard” on a saved definition.</p> : (
                        <p className="text-sm">
                            <a href={workspaceHref('study')} className="font-medium underline-offset-4 hover:underline">{data.study.due} {data.study.due === 1 ? 'card' : 'cards'} due</a>
                            <span className="text-zinc-600"> · {data.study.total} in all · {data.study.reviewed_today} reviewed today</span>
                        </p>
                    )}
                </Panel>
                <Panel title="Saved searches" icon={<Bell className="h-4 w-4" />}>
                    {savedSearches.length === 0 ? <p className="text-sm text-zinc-500">None. After a search, choose “Tell me when a new book answers this”.</p> : (
                        <ul className="space-y-1 text-sm">
                            {savedSearches.map((search) => (
                                <li key={search.id} className="flex items-center justify-between gap-2">
                                    <span className="truncate">{search.query}</span>
                                    <button type="button" aria-label={`Stop watching “${search.query}”`} onClick={() => { setSavedSearches((all) => all.filter((item) => item.id !== search.id)); deleteSavedSearch(search.id).catch(() => undefined) }}><X className="h-4 w-4" /></button>
                                </li>
                            ))}
                        </ul>
                    )}
                </Panel>
                <Panel title="Projects" icon={<FolderKanban className="h-4 w-4" />}>
                    {data.projects.length === 0 ? EMPTY : (
                        <ul className="space-y-1 text-sm">
                            {data.projects.map((project) => (
                                <li key={project.id}><a href={workspaceHref('projects', project.id)} className="underline-offset-4 hover:underline">{project.title}</a> <span className="text-zinc-500">({project.book_count} books)</span></li>
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

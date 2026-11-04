import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Plus, Trash2 } from 'lucide-react'
import { createNote, deleteNote, listNotes, updateNote } from '../../lib/workspaceApi'
import { workspaceHref } from '../../lib/route'
import type { Note } from '../../types/workspace'

const FIELD = 'w-full border-2 border-zinc-950 px-3 py-2 text-sm outline-none focus:bg-zinc-50'

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

function Excerpt({ note }: { note: Note }) {
    return (
        <>
            <p className="truncate font-semibold">{note.title || note.body.slice(0, 60)}</p>
            {note.title && <p className="line-clamp-2 text-sm text-zinc-600">{note.body}</p>}
            {note.concept_links.length > 0 && <p className="mt-1 text-xs text-zinc-500">{note.concept_links.map((name) => `[[${name}]]`).join(' ')}</p>}
        </>
    )
}

function NoteEditor({ id }: { id: number }) {
    const [note, setNote] = useState<Note | null>(null)
    const [title, setTitle] = useState('')
    const [body, setBody] = useState('')
    const [state, setState] = useState<'saved' | 'saving' | 'error'>('saved')
    const [error, setError] = useState<string | null>(null)
    const [backlinks, setBacklinks] = useState<Note[]>([])
    const timer = useRef<number | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        fetch(`/api/workspace/notes/${id}`, { signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(((await response.json()) as { detail?: string }).detail ?? 'Not found.')
                return (await response.json()) as Note
            })
            .then((data) => {
                setNote(data)
                setTitle(data.title)
                setBody(data.body)
            })
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [id])

    // Notes that link the same concepts as this one (their shared backlinks).
    const firstLink = note?.concept_links[0]
    useEffect(() => {
        if (!firstLink) return
        const controller = new AbortController()
        listNotes({ concept: firstLink }, controller.signal)
            .then((all) => setBacklinks(all.filter((other) => other.id !== id)))
            .catch(() => undefined)
        return () => controller.abort()
    }, [firstLink, id])

    useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

    const change = (nextTitle: string, nextBody: string) => {
        setTitle(nextTitle)
        setBody(nextBody)
        setState('saving')
        if (timer.current) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => {
            updateNote(id, { title: nextTitle, body: nextBody })
                .then((saved) => {
                    setNote(saved)
                    setState('saved')
                    setError(null)
                })
                .catch((failure: unknown) => {
                    setState('error')
                    setError(message(failure))
                })
        }, 700)
    }

    const remove = async () => {
        if (!window.confirm('Delete this note?')) return
        await deleteNote(id)
        window.location.hash = workspaceHref('notes')
    }

    if (error && !note) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!note) return <p className="text-sm text-zinc-500">Loading…</p>

    return (
        <div className="space-y-4">
            <a href={workspaceHref('notes')} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"><ArrowLeft className="h-4 w-4" /> All notes</a>
            <input className={`${FIELD} text-lg font-semibold`} value={title} onChange={(event) => change(event.target.value, body)} maxLength={200} placeholder="Title" aria-label="Note title" />
            <textarea className={`${FIELD} min-h-72 leading-relaxed`} value={body} onChange={(event) => change(title, event.target.value)} placeholder="Write here. Link a concept with [[double brackets]]." aria-label="Note text" />
            <div className="flex items-center justify-between text-sm">
                <span role="status" className={state === 'error' ? 'text-red-700' : 'text-zinc-500'}>
                    {state === 'saving' ? 'Saving…' : state === 'error' ? error : 'Saved'}
                </span>
                <button type="button" onClick={() => void remove()} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Delete note</button>
            </div>
            {note.concept_links.length > 0 && (
                <p className="text-sm text-zinc-600">
                    Concepts: {note.concept_links.map((name) => (
                        <a key={name} href={`#/explore`} className="mr-2 underline underline-offset-4">{name}</a>
                    ))}
                </p>
            )}
            {backlinks.length > 0 && (
                <section className="space-y-2 border-t border-zinc-300 pt-3">
                    <h3 className="text-xs uppercase tracking-[0.16em] text-zinc-500">Other notes linking [[{firstLink}]]</h3>
                    <ul className="space-y-2">
                        {backlinks.map((other) => (
                            <li key={other.id}><a href={workspaceHref('notes', other.id)} className="block border border-zinc-300 p-2 hover:border-zinc-950"><Excerpt note={other} /></a></li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    )
}

export function NotesView({ id }: { id: number | null }) {
    const [notes, setNotes] = useState<Note[] | null>(null)
    const [query, setQuery] = useState('')
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        if (id !== null) return
        const controller = new AbortController()
        const handle = window.setTimeout(() => {
            listNotes({ q: query.trim() || undefined }, controller.signal)
                .then(setNotes)
                .catch((failure: unknown) => {
                    if (!controller.signal.aborted) setError(message(failure))
                })
        }, query ? 250 : 0)
        return () => {
            window.clearTimeout(handle)
            controller.abort()
        }
    }, [id, query])

    if (id !== null) return <NoteEditor id={id} />

    const create = async () => {
        try {
            const created = await createNote('Untitled note', '')
            window.location.hash = workspaceHref('notes', created.id)
        } catch (failure) {
            setError(message(failure))
        }
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
                <input className={`${FIELD} min-w-0 flex-1`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search your notes" aria-label="Search your notes" />
                <button type="button" onClick={() => void create()} className="inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-4 text-sm font-medium text-white">
                    <Plus className="h-4 w-4" /> New note
                </button>
            </div>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {notes === null ? (
                <p className="text-sm text-zinc-500">Loading…</p>
            ) : notes.length === 0 ? (
                <p className="text-sm text-zinc-600">{query ? 'No notes match.' : 'No notes yet. Create one, and link concepts with [[double brackets]].'}</p>
            ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {notes.map((note) => (
                        <li key={note.id}><a href={workspaceHref('notes', note.id)} className="block h-full border-2 border-zinc-950 p-4 hover:bg-zinc-50"><Excerpt note={note} /></a></li>
                    ))}
                </ul>
            )}
        </div>
    )
}

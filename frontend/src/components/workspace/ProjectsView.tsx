import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, BookPlus, FolderKanban, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { ResultSkeleton } from '../ResultSkeleton'
import { ResultsView } from '../layouts/ResultsView'
import { listBooks } from '../../lib/libraryApi'
import { libraryHref, workspaceHref } from '../../lib/route'
import { searchRecords } from '../../lib/searchApi'
import {
    addProjectBooks, createNote, createProject, deleteProject, detachFromProject, getProject, listProjects,
    removeProjectBook, updateProject,
} from '../../lib/workspaceApi'
import type { LibraryBook } from '../../types/library'
import type { SearchResponse } from '../../types/search'
import type { Project, ProjectDetail } from '../../types/workspace'
import { EvidenceCard } from './EvidenceCard'

const FIELD = 'border-2 border-zinc-950 px-3 py-2 text-sm outline-none focus:bg-zinc-50'
const PRIMARY = 'inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white disabled:opacity-40'

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

/** Pick books from the library by ticking them. */
function BookPicker({ books, chosen, onChange, exclude = [] }: { books: LibraryBook[]; chosen: number[]; onChange: (ids: number[]) => void; exclude?: number[] }) {
    const [filter, setFilter] = useState('')
    const shown = useMemo(() => {
        const needle = filter.trim().toLowerCase()
        return books.filter((book) => !exclude.includes(book.id) && (!needle || `${book.title} ${book.author ?? ''} ${book.department ?? ''}`.toLowerCase().includes(needle)))
    }, [books, exclude, filter])
    return (
        <div className="space-y-2">
            <input className={`${FIELD} w-full`} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter books by title, author or subject" aria-label="Filter books" />
            <ul className="max-h-56 divide-y divide-zinc-100 overflow-y-auto border-2 border-zinc-950">
                {shown.length === 0 && <li className="px-3 py-2 text-sm text-zinc-500">No books match.</li>}
                {shown.map((book) => (
                    <li key={book.id}>
                        <label className="flex cursor-pointer items-start gap-2 px-3 py-1.5 text-sm hover:bg-zinc-50">
                            <input
                                type="checkbox"
                                className="mt-1"
                                checked={chosen.includes(book.id)}
                                onChange={(event) => onChange(event.target.checked ? [...chosen, book.id] : chosen.filter((id) => id !== book.id))}
                            />
                            <span>
                                <span className="font-medium">{book.title}</span>
                                <span className="text-zinc-500">{book.author ? ` · ${book.author}` : ''}{book.department ? ` · ${book.department}` : ''}</span>
                            </span>
                        </label>
                    </li>
                ))}
            </ul>
        </div>
    )
}

function useLibraryBooks(): LibraryBook[] {
    const [books, setBooks] = useState<LibraryBook[]>([])
    useEffect(() => {
        const controller = new AbortController()
        listBooks(controller.signal).then(setBooks).catch(() => undefined)
        return () => controller.abort()
    }, [])
    return books
}

function ProjectDetailView({ id }: { id: number }) {
    const books = useLibraryBooks()
    const [project, setProject] = useState<ProjectDetail | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [editing, setEditing] = useState(false)
    const [title, setTitle] = useState('')
    const [question, setQuestion] = useState('')
    const [adding, setAdding] = useState(false)
    const [toAdd, setToAdd] = useState<number[]>([])
    const [query, setQuery] = useState('')
    const [response, setResponse] = useState<SearchResponse | null>(null)
    const [searching, setSearching] = useState(false)

    const load = useCallback(
        (signal?: AbortSignal) =>
            getProject(id, signal)
                .then((data) => {
                    setProject(data)
                    setTitle(data.title)
                    setQuestion(data.question)
                })
                .catch((failure: unknown) => {
                    if (!signal?.aborted) setError(message(failure))
                }),
        [id],
    )

    useEffect(() => {
        const controller = new AbortController()
        void load(controller.signal)
        return () => controller.abort()
    }, [load])

    if (error && !project) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!project) return <p className="text-sm text-zinc-500">Loading…</p>

    const bookIds = project.books.map((book) => book.id)
    const act = (work: () => Promise<unknown>) => work().then(() => load()).catch((failure: unknown) => setError(message(failure)))

    const save = async (event: FormEvent) => {
        event.preventDefault()
        await act(async () => {
            await updateProject(id, { title, question })
            setEditing(false)
        })
    }

    const search = async (event: FormEvent) => {
        event.preventDefault()
        if (!query.trim() || bookIds.length === 0) return
        setSearching(true)
        setError(null)
        try {
            setResponse(await searchRecords(query.trim(), bookIds))
        } catch (failure) {
            setResponse(null)
            setError(message(failure))
        } finally {
            setSearching(false)
        }
    }

    const newNote = () =>
        createNote('Untitled note', '', undefined, id)
            .then((note) => { window.location.hash = workspaceHref('notes', note.id) })
            .catch((failure: unknown) => setError(message(failure)))

    const remove = async () => {
        if (!window.confirm(`Delete the project “${project.title}”? Its saved items and notes stay in your workspace.`)) return
        await deleteProject(id)
        window.location.hash = workspaceHref('projects')
    }

    return (
        <div className="space-y-6">
            <a href={workspaceHref('projects')} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"><ArrowLeft className="h-4 w-4" /> All projects</a>
            <header className="space-y-2 border-b-2 border-zinc-950 pb-4">
                {editing ? (
                    <form onSubmit={(event) => void save(event)} className="space-y-2">
                        <input className={`${FIELD} w-full text-lg font-semibold`} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} aria-label="Project title" />
                        <textarea className={`${FIELD} w-full`} value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={1000} rows={2} placeholder="The question this project answers" aria-label="Research question" />
                        <button type="submit" disabled={!title.trim()} className={PRIMARY}>Save</button>
                    </form>
                ) : (
                    <>
                        <p className="text-xs uppercase tracking-[0.18em] text-zinc-500">Research project</p>
                        <h2 className="text-2xl font-semibold tracking-tight">{project.title}</h2>
                        {project.question && <p className="text-zinc-700">{project.question}</p>}
                    </>
                )}
                <div className="flex gap-4 text-sm">
                    <button type="button" onClick={() => setEditing((on) => !on)} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Pencil className="h-4 w-4" /> Edit</button>
                    <button type="button" onClick={() => void remove()} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Delete project</button>
                </div>
            </header>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}

            <section className="space-y-3">
                <h3 className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">
                    Books in this project ({project.books.length})
                    <button type="button" onClick={() => { setAdding((on) => !on); setToAdd([]) }} className="inline-flex items-center gap-1 normal-case tracking-normal underline-offset-4 hover:underline"><BookPlus className="h-4 w-4" /> Add books</button>
                </h3>
                {adding && (
                    <div className="space-y-2">
                        <BookPicker books={books} chosen={toAdd} onChange={setToAdd} exclude={bookIds} />
                        <button type="button" disabled={toAdd.length === 0} onClick={() => void act(async () => { await addProjectBooks(id, toAdd); setAdding(false); setToAdd([]) })} className={PRIMARY}>Add {toAdd.length || ''} selected</button>
                    </div>
                )}
                {project.books.length === 0 ? (
                    <p className="text-sm text-zinc-600">No books yet. Add the books this question should be answered from; searches below look only in them.</p>
                ) : (
                    <ul className="flex flex-wrap gap-2 text-sm">
                        {project.books.map((book) => (
                            <li key={book.id} className="inline-flex items-center gap-1 border border-zinc-300 py-0.5 pl-2 pr-1">
                                <a href={libraryHref(book.id)} className="underline-offset-4 hover:underline">{book.title}</a>
                                <button type="button" aria-label={`Remove ${book.title} from the project`} onClick={() => void act(() => removeProjectBook(id, book.id))} className="p-0.5 hover:text-red-700"><X className="h-3.5 w-3.5" /></button>
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            <section className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">Search these books</h3>
                <form onSubmit={(event) => void search(event)} className="flex flex-col gap-3 sm:flex-row" role="search">
                    <input className={`${FIELD} h-12 flex-1`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={bookIds.length ? 'Ask something these books answer' : 'Add books first'} disabled={bookIds.length === 0} aria-label="Search the project's books" />
                    <button type="submit" disabled={!query.trim() || searching || bookIds.length === 0} className={`${PRIMARY} h-12`}><Search className="h-4 w-4" /> Search</button>
                </form>
                {searching && <ResultSkeleton />}
                {!searching && response && response.results.length === 0 && <p className="border-2 border-zinc-950 p-4 text-sm">None of this project’s books answers that.</p>}
                {!searching && response && response.results.length > 0 && <ResultsView results={response.results} layout={response.layout} query={response.query} />}
            </section>

            <section className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">Evidence ({project.evidence.length})</h3>
                {project.evidence.length === 0 ? (
                    <p className="text-sm text-zinc-600">Use “Save” on a search result or highlight in the reader, and choose this project.</p>
                ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                        {project.evidence.map((item) => (
                            <EvidenceCard key={item.id} item={item} removeLabel="Remove" onRemove={() => void act(() => detachFromProject(id, item.id))} />
                        ))}
                    </div>
                )}
            </section>

            <section className="space-y-3">
                <h3 className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.16em] text-zinc-700">
                    Notes ({project.notes.length})
                    <button type="button" onClick={() => void newNote()} className="inline-flex items-center gap-1 normal-case tracking-normal underline-offset-4 hover:underline"><Plus className="h-4 w-4" /> New note</button>
                </h3>
                {project.notes.length > 0 && (
                    <ul className="grid gap-3 sm:grid-cols-2">
                        {project.notes.map((note) => (
                            <li key={note.id}><a href={workspaceHref('notes', note.id)} className="block h-full border-2 border-zinc-950 p-3 text-sm hover:bg-zinc-50"><p className="truncate font-semibold">{note.title || note.body.slice(0, 60)}</p>{note.title && <p className="line-clamp-2 text-zinc-600">{note.body}</p>}</a></li>
                        ))}
                    </ul>
                )}
            </section>
        </div>
    )
}

export function ProjectsView({ id }: { id: number | null }) {
    const books = useLibraryBooks()
    const [projects, setProjects] = useState<Project[] | null>(null)
    const [creating, setCreating] = useState(false)
    const [title, setTitle] = useState('')
    const [question, setQuestion] = useState('')
    const [chosen, setChosen] = useState<number[]>([])
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        if (id !== null) return
        const controller = new AbortController()
        listProjects(controller.signal)
            .then(setProjects)
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [id])

    if (id !== null) return <ProjectDetailView id={id} />

    const create = async (event: FormEvent) => {
        event.preventDefault()
        setError(null)
        try {
            const created = await createProject(title, question, chosen)
            window.location.hash = workspaceHref('projects', created.id)
        } catch (failure) {
            setError(message(failure))
        }
    }

    return (
        <div className="space-y-4">
            {!creating ? (
                <button type="button" onClick={() => setCreating(true)} className={PRIMARY}><FolderKanban className="h-4 w-4" /> New project</button>
            ) : (
                <form onSubmit={(event) => void create(event)} className="space-y-3 border-2 border-zinc-950 p-4">
                    <h2 className="font-semibold">New research project</h2>
                    <input className={`${FIELD} w-full`} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} placeholder="Title, e.g. “Sorting algorithms compared”" aria-label="Project title" autoFocus />
                    <textarea className={`${FIELD} w-full`} value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={1000} rows={2} placeholder="The question this project answers (optional)" aria-label="Research question" />
                    <p className="text-sm text-zinc-600">Books to search ({chosen.length} chosen). You can change them later.</p>
                    <BookPicker books={books} chosen={chosen} onChange={setChosen} />
                    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
                    <div className="flex gap-3">
                        <button type="submit" disabled={!title.trim()} className={PRIMARY}>Create project</button>
                        <button type="button" onClick={() => setCreating(false)} className="border-2 border-zinc-950 px-4 py-2 text-sm">Cancel</button>
                    </div>
                </form>
            )}
            {!creating && error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {projects === null ? (
                <p className="text-sm text-zinc-500">Loading…</p>
            ) : projects.length === 0 ? (
                <p className="text-sm text-zinc-600">No projects yet. A project is a question plus the books that should answer it; searches inside it look only in those books.</p>
            ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {projects.map((project) => (
                        <li key={project.id}>
                            <a href={workspaceHref('projects', project.id)} className="block h-full border-2 border-zinc-950 p-4 hover:bg-zinc-50">
                                <p className="truncate font-semibold">{project.title}</p>
                                {project.question && <p className="line-clamp-2 text-sm text-zinc-600">{project.question}</p>}
                                <p className="mt-2 text-xs text-zinc-500">{project.book_count} books · {project.evidence_count} saved · {project.note_count} notes</p>
                            </a>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, FolderPlus, Pencil, Trash2 } from 'lucide-react'
import { createCollection, deleteCollection, getCollection, listCollections, removeFromCollection, updateCollection } from '../../lib/workspaceApi'
import { workspaceHref } from '../../lib/route'
import type { Collection, Evidence } from '../../types/workspace'
import { EvidenceCard } from './EvidenceCard'

const FIELD = 'border-2 border-zinc-950 px-3 py-2 text-sm outline-none focus:bg-zinc-50'

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

function CollectionDetail({ id }: { id: number }) {
    const [collection, setCollection] = useState<(Collection & { items: Evidence[] }) | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [editing, setEditing] = useState(false)
    const [name, setName] = useState('')

    const load = useCallback(
        (signal?: AbortSignal) =>
            getCollection(id, signal)
                .then((data) => {
                    setCollection(data)
                    setName(data.name)
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

    if (error) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!collection) return <p className="text-sm text-zinc-500">Loading…</p>

    const rename = async (event: FormEvent) => {
        event.preventDefault()
        try {
            await updateCollection(id, { name })
            setEditing(false)
            await load()
        } catch (failure) {
            setError(message(failure))
        }
    }

    const remove = async () => {
        if (!window.confirm(`Delete the collection “${collection.name}”? The saved items stay in your workspace.`)) return
        await deleteCollection(id)
        window.location.hash = workspaceHref('collections')
    }

    return (
        <div className="space-y-4">
            <a href={workspaceHref('collections')} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline">
                <ArrowLeft className="h-4 w-4" /> All collections
            </a>
            <header className="flex flex-wrap items-center justify-between gap-3 border-b-2 border-zinc-950 pb-3">
                {editing ? (
                    <form onSubmit={(event) => void rename(event)} className="flex gap-2">
                        <input className={FIELD} value={name} onChange={(event) => setName(event.target.value)} maxLength={120} aria-label="Collection name" />
                        <button type="submit" className="border-2 border-zinc-950 bg-zinc-950 px-3 text-sm text-white">Save</button>
                    </form>
                ) : (
                    <h2 className="text-xl font-semibold tracking-tight">{collection.name}</h2>
                )}
                <div className="flex gap-4 text-sm">
                    <button type="button" onClick={() => setEditing((on) => !on)} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Pencil className="h-4 w-4" /> Rename</button>
                    <button type="button" onClick={() => void remove()} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Delete collection</button>
                </div>
            </header>
            {collection.items.length === 0 ? (
                <p className="text-sm text-zinc-600">Nothing here yet. Use “Save” on a search result and choose this collection.</p>
            ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                    {collection.items.map((item) => (
                        <EvidenceCard
                            key={item.id}
                            item={item}
                            removeLabel="Remove"
                            onRemove={() => void removeFromCollection(id, item.id).then(() => load()).catch((failure: unknown) => setError(message(failure)))}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}

export function CollectionsView({ id }: { id: number | null }) {
    const [collections, setCollections] = useState<Collection[] | null>(null)
    const [name, setName] = useState('')
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        if (id !== null) return
        const controller = new AbortController()
        listCollections(controller.signal)
            .then(setCollections)
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [id])

    if (id !== null) return <CollectionDetail id={id} />

    const create = async (event: FormEvent) => {
        event.preventDefault()
        setError(null)
        try {
            const created = await createCollection(name)
            setCollections((all) => [created, ...(all ?? [])])
            setName('')
        } catch (failure) {
            setError(message(failure))
        }
    }

    return (
        <div className="space-y-4">
            <form onSubmit={(event) => void create(event)} className="flex flex-wrap gap-2">
                <input className={`${FIELD} min-w-0 flex-1`} value={name} onChange={(event) => setName(event.target.value)} maxLength={120} placeholder="New collection, e.g. “Sorting algorithms”" aria-label="New collection name" />
                <button type="submit" disabled={!name.trim()} className="inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-4 text-sm font-medium text-white disabled:opacity-40">
                    <FolderPlus className="h-4 w-4" /> Create
                </button>
            </form>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {collections === null ? (
                <p className="text-sm text-zinc-500">Loading…</p>
            ) : collections.length === 0 ? (
                <p className="text-sm text-zinc-600">No collections yet. Create one above, or choose “Save” on any search result.</p>
            ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {collections.map((collection) => (
                        <li key={collection.id}>
                            <a href={workspaceHref('collections', collection.id)} className="block border-2 border-zinc-950 p-4 hover:bg-zinc-50">
                                <p className="truncate font-semibold">{collection.name}</p>
                                <p className="text-sm text-zinc-600">{collection.item_count} {collection.item_count === 1 ? 'item' : 'items'}</p>
                            </a>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

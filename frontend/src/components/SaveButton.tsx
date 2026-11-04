import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Bookmark, BookmarkCheck, Check, Plus } from 'lucide-react'
import { useAuth } from '../lib/auth'
import { addToCollection, createCollection, listCollections, saveEvidence } from '../lib/workspaceApi'
import type { Collection, Evidence, EvidenceDraft } from '../types/workspace'

/**
 * "Save" for a piece of evidence: saves it to the signed-in scholar's workspace and optionally files it in a
 * collection. Signed-out visitors get a link to sign in instead.
 */
export function SaveButton({ draft, label = 'Save' }: { draft: EvidenceDraft; label?: string }) {
    const { user } = useAuth()
    const box = useRef<HTMLDivElement>(null)
    const [open, setOpen] = useState(false)
    const [collections, setCollections] = useState<Collection[]>([])
    const [item, setItem] = useState<Evidence | null>(null)
    const [newName, setNewName] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        if (!open) return
        const onPointer = (event: PointerEvent) => {
            if (box.current && !box.current.contains(event.target as Node)) setOpen(false)
        }
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') setOpen(false)
        }
        document.addEventListener('pointerdown', onPointer)
        document.addEventListener('keydown', onKey)
        return () => {
            document.removeEventListener('pointerdown', onPointer)
            document.removeEventListener('keydown', onKey)
        }
    }, [open])

    if (!user) {
        return (
            <a href="#/account" className="inline-flex items-center gap-1 underline-offset-4 hover:underline" title="Sign in to save to your workspace">
                <Bookmark className="h-4 w-4" />
                {label}
            </a>
        )
    }

    const ensureSaved = async (): Promise<Evidence> => item ?? saveEvidence(draft)

    const run = async (action: (saved: Evidence) => Promise<Evidence | void>) => {
        setBusy(true)
        setError(null)
        try {
            const saved = await ensureSaved()
            setItem((await action(saved)) ?? saved)
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : 'Could not save.')
        } finally {
            setBusy(false)
        }
    }

    const toggle = () => {
        const next = !open
        setOpen(next)
        if (next) {
            setError(null)
            listCollections()
                .then(setCollections)
                .catch(() => setError('Could not load your collections.'))
        }
    }

    const inCollection = (collection: Collection) => item?.collection_ids.includes(collection.id) ?? false

    const file = (collection: Collection) =>
        run(async (saved) => {
            await addToCollection(collection.id, saved.id)
            setCollections((all) => all.map((c) => (c.id === collection.id ? { ...c, item_count: c.item_count + 1 } : c)))
            return { ...saved, collection_ids: [...new Set([...saved.collection_ids, collection.id])] }
        })

    const createAndFile = async (event: FormEvent) => {
        event.preventDefault()
        const name = newName.trim()
        if (!name) return
        await run(async (saved) => {
            const collection = await createCollection(name)
            await addToCollection(collection.id, saved.id)
            setCollections((all) => [{ ...collection, item_count: 1 }, ...all])
            setNewName('')
            return { ...saved, collection_ids: [...saved.collection_ids, collection.id] }
        })
    }

    return (
        <div ref={box} className="relative">
            <button type="button" onClick={toggle} aria-expanded={open} className="inline-flex items-center gap-1 underline-offset-4 hover:underline">
                {item ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}
                {item ? 'Saved' : label}
            </button>
            {open && (
                <div role="dialog" aria-label="Save to your workspace" className="absolute bottom-full right-0 z-20 mb-2 w-72 border-2 border-zinc-950 bg-white p-3 text-sm shadow-lg">
                    <button
                        type="button"
                        disabled={busy || item !== null}
                        onClick={() => void run(async () => undefined)}
                        className="flex w-full items-center justify-between border border-zinc-300 px-2 py-1.5 text-left hover:border-zinc-950 disabled:opacity-60"
                    >
                        <span>{item ? 'Saved to your workspace' : 'Save without a collection'}</span>
                        {item && <Check className="h-4 w-4" />}
                    </button>
                    {collections.length > 0 && <p className="mb-1 mt-3 text-xs uppercase tracking-[0.14em] text-zinc-500">Add to collection</p>}
                    <ul className="max-h-40 space-y-1 overflow-y-auto">
                        {collections.map((collection) => (
                            <li key={collection.id}>
                                <button
                                    type="button"
                                    disabled={busy || inCollection(collection)}
                                    onClick={() => void file(collection)}
                                    className="flex w-full items-center justify-between px-2 py-1 text-left hover:bg-zinc-100 disabled:opacity-60"
                                >
                                    <span className="truncate">{collection.name}</span>
                                    {inCollection(collection) ? <Check className="h-4 w-4 shrink-0" /> : <span className="text-xs text-zinc-500">{collection.item_count}</span>}
                                </button>
                            </li>
                        ))}
                    </ul>
                    <form onSubmit={(event) => void createAndFile(event)} className="mt-3 flex gap-2">
                        <input
                            value={newName}
                            onChange={(event) => setNewName(event.target.value)}
                            maxLength={120}
                            placeholder="New collection"
                            aria-label="New collection name"
                            className="min-w-0 flex-1 border border-zinc-300 px-2 py-1 outline-none focus:border-zinc-950"
                        />
                        <button type="submit" disabled={busy || !newName.trim()} className="border border-zinc-950 px-2 disabled:opacity-40" aria-label="Create collection and add">
                            <Plus className="h-4 w-4" />
                        </button>
                    </form>
                    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
                </div>
            )}
        </div>
    )
}

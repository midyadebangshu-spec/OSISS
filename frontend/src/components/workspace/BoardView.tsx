import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent } from 'react'
import { AlertTriangle, ArrowLeft, Link2, Maximize2, PenLine, Plus, Trash2, X } from 'lucide-react'
import { DEFAULT_HEIGHT, NODE_WIDTH, RELATIONS, RELATION_STYLE, clampScale, edgeEndpoints, fitView, toBoard } from '../../lib/board'
import type { Box } from '../../lib/board'
import { pageLabel } from '../../lib/results'
import { readerHref, workspaceHref } from '../../lib/route'
import { searchConcepts } from '../../lib/conceptsApi'
import {
    addBoardEdge, addBoardNode, boardOutline, deleteBoardEdge, deleteBoardNode, getBoard, getProject, moveBoardNode, seedBoardConcept,
} from '../../lib/workspaceApi'
import type { ConceptSummary } from '../../types/concepts'
import type { Board, BoardNode, BoardRelation, ProjectDetail } from '../../types/workspace'

const FIELD = 'border-2 border-zinc-950 px-2 py-1.5 text-sm outline-none focus:bg-zinc-50'
const KIND_LABEL = { claim: 'Claim', evidence: 'Evidence', concept: 'Concept', note: 'Note' } as const

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

function NodeBody({ node, unsupported }: { node: BoardNode; unsupported: boolean }) {
    if (node.kind === 'claim') {
        return (
            <>
                <p className="font-medium leading-snug">{node.text}</p>
                {unsupported && <p className="mt-1 flex items-center gap-1 text-xs text-amber-800"><AlertTriangle className="h-3.5 w-3.5" /> No supporting evidence yet</p>}
            </>
        )
    }
    if (node.kind === 'evidence' && node.evidence) {
        const item = node.evidence
        return (
            <>
                <p className="line-clamp-5 leading-snug">“{item.quote}”</p>
                <p className="mt-1 text-xs text-zinc-500">
                    {item.book_title}
                    {item.page_number ? ` · p. ${pageLabel(item.page_number, item.printed_page)}` : ''}
                </p>
                {item.book_id && item.page_number && (
                    <a href={readerHref(item.book_id, item.page_number)} target="_blank" rel="noreferrer" onPointerDown={(event) => event.stopPropagation()} className="text-xs underline underline-offset-4">Open the page</a>
                )}
            </>
        )
    }
    if (node.kind === 'note' && node.note) {
        return (
            <>
                <p className="font-medium">{node.note.title || 'Note'}</p>
                <p className="line-clamp-4 text-zinc-700">{node.note.body}</p>
            </>
        )
    }
    return <p className="font-medium">{node.concept_key}</p>
}

/** The project's research board: claims, evidence, concepts and notes laid out freely and joined by typed links. */
export function BoardView({ projectId }: { projectId: number }) {
    const [board, setBoard] = useState<Board | null>(null)
    const [project, setProject] = useState<ProjectDetail | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [view, setView] = useState({ x: 60, y: 60, scale: 1 })
    const [selected, setSelected] = useState<number | null>(null)
    const [selectedEdge, setSelectedEdge] = useState<number | null>(null)
    const [relation, setRelation] = useState<BoardRelation | null>(null)
    const [linkFrom, setLinkFrom] = useState<number | null>(null)
    const [drag, setDrag] = useState<{ id: number; x: number; y: number } | null>(null)
    const [heights, setHeights] = useState<Record<number, number>>({})
    const [panel, setPanel] = useState<'claim' | 'evidence' | 'notes' | 'concepts'>('claim')
    const [claim, setClaim] = useState('')
    const [conceptQuery, setConceptQuery] = useState('')
    const [concepts, setConcepts] = useState<ConceptSummary[]>([])
    const [editing, setEditing] = useState<{ id: number; text: string } | null>(null)
    const stage = useRef<HTMLDivElement>(null)
    const observer = useRef<ResizeObserver | null>(null)
    const elements = useRef(new Map<number, HTMLElement>())
    const gesture = useRef<{ kind: 'pan' | 'node'; id?: number; startX: number; startY: number; originX: number; originY: number; moved: boolean } | null>(null)
    const fitted = useRef(false)

    const load = useCallback(
        (signal?: AbortSignal) =>
            Promise.all([getBoard(projectId, signal), getProject(projectId, signal)])
                .then(([nextBoard, nextProject]) => {
                    setBoard(nextBoard)
                    setProject(nextProject)
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
    }, [load])

    useEffect(() => {
        observer.current = new ResizeObserver((entries) => {
            setHeights((current) => {
                const next = { ...current }
                let changed = false
                for (const entry of entries) {
                    const id = Number((entry.target as HTMLElement).dataset.nodeId)
                    const height = Math.round(entry.contentRect.height + 2)
                    if (next[id] !== height) {
                        next[id] = height
                        changed = true
                    }
                }
                return changed ? next : current
            })
        })
        elements.current.forEach((element) => observer.current?.observe(element))
        return () => observer.current?.disconnect()
    }, [])

    const register = useCallback((id: number, element: HTMLElement | null) => {
        const known = elements.current.get(id)
        if (known && known !== element) observer.current?.unobserve(known)
        if (element) {
            elements.current.set(id, element)
            observer.current?.observe(element)
        } else {
            elements.current.delete(id)
        }
    }, [])

    const position = useCallback((node: BoardNode) => (drag && drag.id === node.id ? { x: drag.x, y: drag.y } : { x: node.x, y: node.y }), [drag])
    const boxOf = useCallback((node: BoardNode): Box => ({ ...position(node), width: NODE_WIDTH, height: heights[node.id] ?? DEFAULT_HEIGHT }), [position, heights])

    // Fit the view to the board once it has loaded.
    useEffect(() => {
        if (!board || fitted.current || !stage.current) return
        fitted.current = true
        const box = stage.current.getBoundingClientRect()
        setView(fitView(board.nodes.map((node) => ({ x: node.x, y: node.y, width: NODE_WIDTH, height: DEFAULT_HEIGHT })), box.width, box.height))
    }, [board])

    const fit = () => {
        if (!board || !stage.current) return
        const box = stage.current.getBoundingClientRect()
        setView(fitView(board.nodes.map(boxOf), box.width, box.height))
    }

    const nodesById = useMemo(() => new Map((board?.nodes ?? []).map((node) => [node.id, node])), [board])
    const unsupported = useMemo(() => new Set(board?.unsupported ?? []), [board])
    const onBoard = useMemo(() => ({
        evidence: new Set((board?.nodes ?? []).flatMap((n) => (n.evidence ? [n.evidence.id] : []))),
        notes: new Set((board?.nodes ?? []).flatMap((n) => (n.note ? [n.note.id] : []))),
    }), [board])

    const act = async (work: () => Promise<unknown>) => {
        try {
            setError(null)
            await work()
            await load()
        } catch (failure) {
            setError(message(failure))
        }
    }

    const centre = (): { x: number; y: number } => {
        const box = stage.current?.getBoundingClientRect()
        const [x, y] = toBoard(view, (box?.width ?? 600) / 2, (box?.height ?? 400) / 2)
        const jitter = ((board?.nodes.length ?? 0) % 5) * 24
        return { x: Math.round(x - NODE_WIDTH / 2 + jitter), y: Math.round(y - DEFAULT_HEIGHT / 2 + jitter) }
    }

    // --- pointer handling ---
    const onStagePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) return
        gesture.current = { kind: 'pan', startX: event.clientX, startY: event.clientY, originX: view.x, originY: view.y, moved: false }
        event.currentTarget.setPointerCapture(event.pointerId)
    }

    const onNodePointerDown = (event: ReactPointerEvent<HTMLElement>, node: BoardNode) => {
        if (event.button !== 0) return
        event.stopPropagation()
        gesture.current = { kind: 'node', id: node.id, startX: event.clientX, startY: event.clientY, originX: node.x, originY: node.y, moved: false }
        stage.current?.setPointerCapture(event.pointerId)
    }

    const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
        const current = gesture.current
        if (!current) return
        const dx = event.clientX - current.startX
        const dy = event.clientY - current.startY
        if (!current.moved && Math.hypot(dx, dy) < 4) return
        current.moved = true
        if (current.kind === 'pan') setView((v) => ({ ...v, x: current.originX + dx, y: current.originY + dy }))
        else if (current.id !== undefined) setDrag({ id: current.id, x: Math.round(current.originX + dx / view.scale), y: Math.round(current.originY + dy / view.scale) })
    }

    const onPointerUp = () => {
        const current = gesture.current
        gesture.current = null
        if (!current) return
        if (current.kind === 'pan') {
            if (!current.moved) {
                setSelected(null)
                setSelectedEdge(null)
                setLinkFrom(null)
            }
            return
        }
        const node = current.id !== undefined ? nodesById.get(current.id) : undefined
        if (!node) return
        if (current.moved && drag) {
            const moved = drag
            setBoard((b) => (b ? { ...b, nodes: b.nodes.map((n) => (n.id === moved.id ? { ...n, x: moved.x, y: moved.y } : n)) } : b))
            setDrag(null)
            moveBoardNode(projectId, node.id, { x: moved.x, y: moved.y }).catch((failure: unknown) => setError(message(failure)))
            return
        }
        setDrag(null)
        setSelectedEdge(null)
        if (relation && linkFrom !== null && linkFrom !== node.id) {
            const from = linkFrom
            setLinkFrom(null)
            void act(() => addBoardEdge(projectId, from, node.id, relation))
        } else if (relation) {
            setLinkFrom(node.id)
            setSelected(node.id)
        } else {
            setSelected(node.id)
        }
    }

    const onWheel = (event: ReactWheelEvent<HTMLDivElement>) => {
        const box = stage.current?.getBoundingClientRect()
        if (!box) return
        const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1
        setView((v) => {
            const scale = clampScale(v.scale * factor)
            const px = event.clientX - box.left
            const py = event.clientY - box.top
            const [bx, by] = toBoard(v, px, py)
            return { scale, x: px - bx * scale, y: py - by * scale }
        })
    }

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if ((event.target as HTMLElement).closest('input, textarea, select')) return
            if (event.key === 'Escape') {
                setRelation(null)
                setLinkFrom(null)
            }
            if ((event.key === 'Delete' || event.key === 'Backspace') && selected !== null) {
                void act(() => deleteBoardNode(projectId, selected).then(() => setSelected(null)))
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selected, projectId])

    useEffect(() => {
        const query = conceptQuery.trim()
        if (query.length < 2) return
        const controller = new AbortController()
        const timer = window.setTimeout(() => {
            searchConcepts(query, controller.signal, 8).then((result) => setConcepts(result.concepts)).catch(() => undefined)
        }, 250)
        return () => {
            window.clearTimeout(timer)
            controller.abort()
        }
    }, [conceptQuery])

    if (error && !board) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!board || !project) return <p className="text-sm text-zinc-500">Loading…</p>

    const makeDraft = async () => {
        try {
            const draft = await boardOutline(projectId)
            window.location.hash = workspaceHref('writing', draft.id)
        } catch (failure) {
            setError(message(failure))
        }
    }

    const edges = board.edges
        .map((edge) => ({ edge, from: nodesById.get(edge.source_id), to: nodesById.get(edge.target_id) }))
        .filter((entry): entry is { edge: typeof entry.edge; from: BoardNode; to: BoardNode } => Boolean(entry.from && entry.to))

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <a href={workspaceHref('projects', projectId)} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"><ArrowLeft className="h-4 w-4" /> {project.title}</a>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="inline-flex items-center gap-1 text-zinc-500"><Link2 className="h-4 w-4" /> Link:</span>
                    {RELATIONS.map((item) => (
                        <button
                            key={item}
                            type="button"
                            aria-pressed={relation === item}
                            onClick={() => { setRelation(relation === item ? null : item); setLinkFrom(null) }}
                            className={`border-2 px-2 py-0.5 ${relation === item ? 'bg-zinc-950 text-white' : ''}`}
                            style={{ borderColor: RELATION_STYLE[item].color }}
                        >
                            {RELATION_STYLE[item].label}
                        </button>
                    ))}
                    <button type="button" onClick={fit} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Maximize2 className="h-4 w-4" /> Fit</button>
                    <button type="button" onClick={() => void makeDraft()} className="inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-3 py-1 text-white"><PenLine className="h-4 w-4" /> Make a draft</button>
                </div>
            </div>
            <p role="status" className="min-h-5 text-sm text-zinc-600">
                {relation && (linkFrom === null ? `Click the node the link starts from (“${RELATION_STYLE[relation].label}”).` : 'Now click the node it points to.')}
                {relation && ' Press Escape to stop linking.'}
            </p>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {board.unsupported.length > 0 && (
                <p className="flex items-center gap-1 text-sm text-amber-800"><AlertTriangle className="h-4 w-4" /> {board.unsupported.length} {board.unsupported.length === 1 ? 'claim has' : 'claims have'} no supporting evidence.</p>
            )}

            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
                <div
                    ref={stage}
                    onPointerDown={onStagePointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onWheel={onWheel}
                    className="relative h-[68vh] min-h-96 touch-none select-none overflow-hidden border-2 border-zinc-950 bg-zinc-50"
                    style={{ backgroundImage: 'radial-gradient(#d4d4d8 1px, transparent 1px)', backgroundSize: `${24 * view.scale}px ${24 * view.scale}px`, backgroundPosition: `${view.x}px ${view.y}px` }}
                    aria-label="Research board"
                >
                    <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
                        <svg className="absolute left-0 top-0 overflow-visible" width="1" height="1" aria-hidden="true">
                            <defs>
                                {RELATIONS.map((item) => (
                                    <marker key={item} id={`arrow-${item}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                                        <path d="M0 0 L10 5 L0 10 z" fill={RELATION_STYLE[item].color} />
                                    </marker>
                                ))}
                            </defs>
                            {edges.map(({ edge, from, to }) => {
                                const line = edgeEndpoints(boxOf(from), boxOf(to))
                                const style = RELATION_STYLE[edge.relation]
                                const active = selectedEdge === edge.id
                                return (
                                    <g key={edge.id} className="cursor-pointer" onPointerDown={(event) => { event.stopPropagation(); setSelectedEdge(edge.id); setSelected(null) }}>
                                        <line x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2} stroke="transparent" strokeWidth={14} />
                                        <line x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2} stroke={style.color} strokeWidth={active ? 4 : 2} strokeDasharray={style.dash} markerEnd={`url(#arrow-${edge.relation})`} />
                                        <text x={(line.x1 + line.x2) / 2} y={(line.y1 + line.y2) / 2 - 6} textAnchor="middle" fontSize="11" fill={style.color} className="select-none">{edge.note || style.label}</text>
                                    </g>
                                )
                            })}
                        </svg>
                        {board.nodes.map((node) => {
                            const at = position(node)
                            const isSelected = selected === node.id
                            const weak = node.kind === 'claim' && unsupported.has(node.id)
                            return (
                                <div
                                    key={node.id}
                                    data-node-id={node.id}
                                    ref={(element) => register(node.id, element)}
                                    onPointerDown={(event) => onNodePointerDown(event, node)}
                                    className={`absolute cursor-grab border-2 bg-white p-2 text-sm shadow-sm active:cursor-grabbing ${weak ? 'border-amber-500' : 'border-zinc-950'} ${isSelected ? 'ring-2 ring-sky-500 ring-offset-2' : ''} ${linkFrom === node.id ? 'ring-2 ring-green-600 ring-offset-2' : ''}`}
                                    style={{ left: at.x, top: at.y, width: NODE_WIDTH }}
                                >
                                    <p className="mb-1 text-[10px] uppercase tracking-[0.16em] text-zinc-500">{KIND_LABEL[node.kind]}</p>
                                    {editing?.id === node.id ? (
                                        <form onPointerDown={(event) => event.stopPropagation()} onSubmit={(event) => { event.preventDefault(); void act(() => moveBoardNode(projectId, node.id, { text: editing.text }).then(() => setEditing(null))) }} className="space-y-1">
                                            <textarea autoFocus className={`${FIELD} w-full`} value={editing.text} maxLength={1000} onChange={(event) => setEditing({ id: node.id, text: event.target.value })} aria-label="Claim text" />
                                            <button type="submit" className="border border-zinc-950 px-2 text-xs">Save</button>
                                        </form>
                                    ) : (
                                        <NodeBody node={node} unsupported={weak} />
                                    )}
                                    {isSelected && editing?.id !== node.id && (
                                        <p className="mt-2 flex gap-3 text-xs" onPointerDown={(event) => event.stopPropagation()}>
                                            {node.kind === 'claim' && <button type="button" onClick={() => setEditing({ id: node.id, text: node.text })} className="underline-offset-4 hover:underline">Edit</button>}
                                            <button type="button" onClick={() => void act(() => deleteBoardNode(projectId, node.id).then(() => setSelected(null)))} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-3 w-3" /> Remove from board</button>
                                        </p>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                    {board.nodes.length === 0 && <p className="pointer-events-none absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-zinc-500">The board is empty. Add a claim, saved evidence, notes or a concept from the panel on the right.</p>}
                    {selectedEdge !== null && (
                        <div className="absolute bottom-2 left-2 flex items-center gap-3 border-2 border-zinc-950 bg-white px-3 py-1.5 text-sm" onPointerDown={(event) => event.stopPropagation()}>
                            Link selected
                            <button type="button" onClick={() => void act(() => deleteBoardEdge(projectId, selectedEdge).then(() => setSelectedEdge(null)))} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Remove link</button>
                            <button type="button" aria-label="Close" onClick={() => setSelectedEdge(null)}><X className="h-4 w-4" /></button>
                        </div>
                    )}
                </div>

                <aside className="space-y-3">
                    <div className="flex flex-wrap gap-x-3 border-b border-zinc-200 text-sm">
                        {(['claim', 'evidence', 'notes', 'concepts'] as const).map((item) => (
                            <button key={item} type="button" aria-pressed={panel === item} onClick={() => setPanel(item)} className={`border-b-2 px-1 py-1 font-medium ${panel === item ? 'border-zinc-950' : 'border-transparent text-zinc-500'}`}>
                                {item === 'claim' ? 'Claim' : item === 'evidence' ? 'Evidence' : item === 'notes' ? 'Notes' : 'Concepts'}
                            </button>
                        ))}
                    </div>
                    {panel === 'claim' && (
                        <form onSubmit={(event) => { event.preventDefault(); const text = claim.trim(); if (!text) return; setClaim(''); void act(() => addBoardNode(projectId, { kind: 'claim', text, ...centre() })) }} className="space-y-2">
                            <textarea className={`${FIELD} w-full`} rows={3} value={claim} maxLength={1000} onChange={(event) => setClaim(event.target.value)} placeholder="A claim you will argue, in your own words" aria-label="New claim" />
                            <button type="submit" disabled={!claim.trim()} className="inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-3 py-1.5 text-sm text-white disabled:opacity-40"><Plus className="h-4 w-4" /> Add claim</button>
                        </form>
                    )}
                    {panel === 'evidence' && (
                        <ul className="max-h-[52vh] space-y-2 overflow-y-auto">
                            {project.evidence.length === 0 && <li className="text-sm text-zinc-600">This project has no saved items yet. Use “Save” on results and choose this project.</li>}
                            {project.evidence.map((item) => (
                                <li key={item.id} className="space-y-1 border border-zinc-300 p-2 text-sm">
                                    <p className="line-clamp-3">“{item.quote || item.kind}”</p>
                                    <p className="text-xs text-zinc-500">{item.book_title}{item.page_number ? ` · p. ${item.printed_page ?? item.page_number}` : ''}</p>
                                    <button type="button" disabled={onBoard.evidence.has(item.id)} onClick={() => void act(() => addBoardNode(projectId, { kind: 'evidence', evidence_id: item.id, ...centre() }))} className="text-xs underline-offset-4 hover:underline disabled:no-underline disabled:opacity-50">{onBoard.evidence.has(item.id) ? 'On the board' : 'Put on board'}</button>
                                </li>
                            ))}
                        </ul>
                    )}
                    {panel === 'notes' && (
                        <ul className="max-h-[52vh] space-y-2 overflow-y-auto">
                            {project.notes.length === 0 && <li className="text-sm text-zinc-600">No notes in this project yet.</li>}
                            {project.notes.map((note) => (
                                <li key={note.id} className="space-y-1 border border-zinc-300 p-2 text-sm">
                                    <p className="font-medium">{note.title || note.body.slice(0, 40)}</p>
                                    <button type="button" disabled={onBoard.notes.has(note.id)} onClick={() => void act(() => addBoardNode(projectId, { kind: 'note', note_id: note.id, ...centre() }))} className="text-xs underline-offset-4 hover:underline disabled:no-underline disabled:opacity-50">{onBoard.notes.has(note.id) ? 'On the board' : 'Put on board'}</button>
                                </li>
                            ))}
                        </ul>
                    )}
                    {panel === 'concepts' && (
                        <div className="space-y-2">
                            <input className={`${FIELD} w-full`} value={conceptQuery} onChange={(event) => setConceptQuery(event.target.value)} placeholder="Find a concept, e.g. stack" aria-label="Find a concept" />
                            <ul className="space-y-2">
                                {conceptQuery.trim().length >= 2 && concepts.map((concept) => (
                                    <li key={concept.id} className="flex items-center justify-between gap-2 border border-zinc-300 p-2 text-sm">
                                        <span className="min-w-0 truncate">{concept.label} <span className="text-xs text-zinc-500">{concept.book_count} books</span></span>
                                        <button type="button" onClick={() => void act(() => seedBoardConcept(projectId, concept.id))} className="shrink-0 text-xs underline-offset-4 hover:underline">Add with neighbours</button>
                                    </li>
                                ))}
                            </ul>
                            <p className="text-xs text-zinc-500">Adds the concept and the concepts most often taught with it, joined as the textbooks relate them.</p>
                        </div>
                    )}
                </aside>
            </div>
        </div>
    )
}

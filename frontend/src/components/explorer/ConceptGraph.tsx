import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { ConceptDetail, Relation } from '../../types/concepts'
import { forceLayout } from '../../lib/forceLayout'
import { RELATION_COLOR, relationPhrase } from '../../lib/relations'

type ConceptGraphProps = {
    concept: ConceptDetail
    selectedId: number | null
    onSelect: (id: number) => void
    onOpen: (id: number) => void
}

function nodeRadius(bookCount: number, center: boolean): number {
    return center ? 18 : Math.min(15, 6 + 2.6 * Math.sqrt(bookCount))
}

function shorten(label: string, max = 22): string {
    return label.length > max ? `${label.slice(0, max - 1)}…` : label
}

/** The open concept in the middle and its strongest neighbours around it; click selects, double-click opens. */
export function ConceptGraph({ concept, selectedId, onSelect, onOpen }: ConceptGraphProps) {
    const box = useRef<HTMLDivElement>(null)
    const [width, setWidth] = useState(360)
    const height = width < 520 ? 360 : 440

    useEffect(() => {
        const element = box.current
        if (!element) return
        const observer = new ResizeObserver(([entry]) => setWidth(Math.max(300, Math.round(entry.contentRect.width))))
        observer.observe(element)
        return () => observer.disconnect()
    }, [])

    const neighbours = useMemo(() => concept.neighbours.slice(0, width < 520 ? 12 : 20), [concept.neighbours, width])
    const maxWeight = Math.max(0.001, ...neighbours.map((n) => n.weight))

    const positions = useMemo(() => {
        const ids = new Set(neighbours.map((n) => n.id))
        const nodes = [
            { id: concept.id, radius: nodeRadius(concept.book_count, true), fixed: true },
            ...neighbours.map((n) => ({ id: n.id, radius: nodeRadius(n.book_count, false) })),
        ]
        const spoke = Math.min(width, height) * 0.36
        const edges = [
            ...neighbours.map((n) => ({
                source: concept.id,
                target: n.id,
                // Stronger links sit closer to the centre.
                length: spoke * (1.15 - 0.45 * (n.weight / maxWeight)),
                strength: 1,
            })),
            ...concept.neighbour_links
                .filter((link) => ids.has(link.a) && ids.has(link.b))
                .map((link) => ({ source: link.a, target: link.b, length: spoke * 0.8, strength: 0.25 })),
        ]
        return forceLayout(nodes, edges, width, height)
    }, [concept.id, concept.book_count, concept.neighbour_links, neighbours, width, height, maxWeight])

    const center = positions.get(concept.id)
    const ids = new Set(neighbours.map((n) => n.id))
    const crossLinks = concept.neighbour_links.filter((link) => ids.has(link.a) && ids.has(link.b))
    const relations = [...new Set(neighbours.map((n) => n.relation).filter(Boolean))] as Relation[]

    const onKey = (event: KeyboardEvent, id: number) => {
        if (event.key === 'Enter') onOpen(id)
        if (event.key === ' ') {
            event.preventDefault()
            onSelect(id)
        }
    }

    return (
        <div>
            <div ref={box} className="w-full min-w-0">
                <svg
                    width="100%"
                    height={height}
                    viewBox={`0 0 ${width} ${height}`}
                    preserveAspectRatio="xMidYMid meet"
                    role="img"
                    aria-label={`Concepts related to ${concept.label}`}
                    className="block select-none"
                >
                    {crossLinks.map((link) => {
                        const a = positions.get(link.a)
                        const b = positions.get(link.b)
                        return a && b ? (
                            <line key={`${link.a}-${link.b}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#e4e4e7" strokeWidth={1} />
                        ) : null
                    })}
                    {center &&
                        neighbours.map((n) => {
                            const p = positions.get(n.id)
                            if (!p) return null
                            const selected = n.id === selectedId
                            const color = n.relation ? RELATION_COLOR[n.relation] : selected ? '#18181b' : '#a1a1aa'
                            return (
                                <line
                                    key={`edge-${n.id}`}
                                    x1={center.x}
                                    y1={center.y}
                                    x2={p.x}
                                    y2={p.y}
                                    stroke={color}
                                    strokeWidth={1 + 4 * (n.weight / maxWeight) + (selected ? 1.5 : 0)}
                                    strokeDasharray={n.relation === 'contrast' ? '6 4' : undefined}
                                    strokeOpacity={selectedId && !selected ? 0.45 : 0.9}
                                    className="cursor-pointer"
                                    onClick={() => onSelect(n.id)}
                                >
                                    <title>{`${concept.label} ${relationPhrase(n)} ${n.label}`}</title>
                                </line>
                            )
                        })}
                    {neighbours.map((n) => {
                        const p = positions.get(n.id)
                        if (!p) return null
                        const r = nodeRadius(n.book_count, false)
                        const selected = n.id === selectedId
                        return (
                            <g
                                key={n.id}
                                transform={`translate(${p.x},${p.y})`}
                                role="button"
                                tabIndex={0}
                                aria-label={`${n.label}: ${relationPhrase(n)}, in ${n.book_count} books. Space selects, Enter opens.`}
                                aria-pressed={selected}
                                className="cursor-pointer outline-none focus-visible:[&>circle]:stroke-amber-500"
                                onClick={() => onSelect(n.id)}
                                onDoubleClick={() => onOpen(n.id)}
                                onKeyDown={(event) => onKey(event, n.id)}
                            >
                                <circle r={r} fill={selected ? '#fcd34d' : '#ffffff'} stroke="#18181b" strokeWidth={selected ? 3 : 2} />
                                <text
                                    y={r + 13}
                                    textAnchor="middle"
                                    className="fill-zinc-900 text-[12px]"
                                    style={{ paintOrder: 'stroke', stroke: '#ffffff', strokeWidth: 4, strokeLinejoin: 'round' }}
                                    fontWeight={selected ? 600 : 400}
                                >
                                    {shorten(n.label)}
                                </text>
                            </g>
                        )
                    })}
                    {center && (
                        <g transform={`translate(${center.x},${center.y})`}>
                            <circle r={nodeRadius(concept.book_count, true)} fill="#18181b" />
                            <text
                                y={nodeRadius(concept.book_count, true) + 16}
                                textAnchor="middle"
                                className="fill-zinc-950 text-[14px] font-semibold"
                                style={{ paintOrder: 'stroke', stroke: '#ffffff', strokeWidth: 5, strokeLinejoin: 'round' }}
                            >
                                {shorten(concept.label, 28)}
                            </text>
                        </g>
                    )}
                </svg>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-zinc-200 px-1 pt-2 text-xs text-zinc-600">
                <span className="inline-flex items-center gap-1.5">
                    <span className="inline-block h-0.5 w-5 bg-zinc-400" /> appears with
                </span>
                {relations.map((relation) => (
                    <span key={relation} className="inline-flex items-center gap-1.5">
                        <span
                            className="inline-block h-0.5 w-5"
                            style={{
                                background: relation === 'contrast' ? `repeating-linear-gradient(90deg, ${RELATION_COLOR[relation]} 0 5px, transparent 5px 8px)` : RELATION_COLOR[relation],
                            }}
                        />
                        {relationPhrase({ relation, direction: 'out' })}
                    </span>
                ))}
                <span className="ml-auto text-zinc-500">Thicker line = stronger link · bigger circle = more books · double-click to open</span>
            </div>
        </div>
    )
}

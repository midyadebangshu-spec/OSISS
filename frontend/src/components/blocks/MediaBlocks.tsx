import { useState } from 'react'
import { Image as ImageIcon, Maximize2, Table2 } from 'lucide-react'
import type { Figure, Formula, PageSnapshot, Rect, Table } from '../../types/search'
import { figureLightboxItem, figureTitle } from '../../lib/results'
import { useViewer } from '../../lib/viewer'
import type { LightboxItem } from '../../lib/viewer'

type FigureViewProps = {
    figure: Figure
    source: string
    gallery?: LightboxItem[]
    index?: number
    size?: 'hero' | 'normal' | 'thumb'
}

/** A figure crop from the book with its caption; opens in the lightbox. */
export function FigureView({ figure, source, gallery, index = 0, size = 'normal' }: FigureViewProps) {
    const { openLightbox } = useViewer()
    const items = gallery ?? [figureLightboxItem(figure, source)]
    const height = size === 'hero' ? 'max-h-[30rem]' : size === 'thumb' ? 'h-28' : 'max-h-72'
    return (
        <figure className="group flex flex-col border-2 border-zinc-950 bg-white">
            <button
                type="button"
                onClick={() => openLightbox(items, index)}
                className="relative flex flex-1 items-center justify-center bg-white p-2"
                aria-label={`Enlarge ${figureTitle(figure)}`}
            >
                <img
                    src={figure.image}
                    alt={figure.caption ?? figureTitle(figure)}
                    loading="lazy"
                    className={`${height} w-auto max-w-full object-contain`}
                    width={figure.width ?? undefined}
                    height={figure.height ?? undefined}
                />
                <span className="absolute right-2 top-2 hidden border border-zinc-950 bg-white p-1 group-hover:block">
                    <Maximize2 className="h-3.5 w-3.5" />
                </span>
            </button>
            {size !== 'thumb' ? (
                <figcaption className="border-t border-zinc-300 px-3 py-2 text-sm">
                    <span className="font-semibold">{figureTitle(figure)}</span>
                    {figure.caption && <span className="text-zinc-700"> · {stripLabel(figure.caption)}</span>}
                </figcaption>
            ) : (
                <figcaption className="truncate border-t border-zinc-300 px-2 py-1 text-xs text-zinc-600">{figureTitle(figure)}</figcaption>
            )}
        </figure>
    )
}

function stripLabel(caption: string): string {
    // "Figure 4.1 Difference between..." -> "Difference between..." (the label is shown separately).
    return caption.replace(/^\s*\S+\s*[.:\-–]?\s*[0-9০-৯०-९]+(?:\s*[.\-–]\s*[0-9০-৯०-९]+)*\s*[.:\-–]?\s*/u, '') || caption
}

/** A table from the book as real rows (selectable, searchable), with the original crop one click away. */
export function TableView({ table, source }: { table: Table; source: string }) {
    const [showOriginal, setShowOriginal] = useState(false)
    const { openLightbox } = useViewer()
    const header = table.header && table.header.some(Boolean) ? table.header : null
    return (
        <div className="border-2 border-zinc-950 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b-2 border-zinc-950 px-3 py-2">
                <p className="text-sm">
                    <span className="font-semibold">{figureTitle(table, 'Table')}</span>
                    {table.caption && <span className="text-zinc-700"> · {stripLabel(table.caption)}</span>}
                </p>
                <button
                    type="button"
                    onClick={() => setShowOriginal((value) => !value)}
                    className="inline-flex items-center gap-1 text-xs underline-offset-4 hover:underline"
                >
                    {showOriginal ? <Table2 className="h-3.5 w-3.5" /> : <ImageIcon className="h-3.5 w-3.5" />}
                    {showOriginal ? 'Show as text' : 'Show original'}
                </button>
            </div>
            {showOriginal ? (
                <button type="button" className="block w-full p-2" onClick={() => openLightbox([figureLightboxItem(table, source, 'Table')], 0)}>
                    <img src={table.image} alt={table.caption ?? 'Table'} className="mx-auto max-h-[32rem] w-auto max-w-full" loading="lazy" />
                </button>
            ) : (
                <div className="max-h-[32rem] overflow-auto">
                    <table className="w-full border-collapse text-left text-sm">
                        {header && (
                            <thead className="sticky top-0 bg-zinc-950 text-white">
                                <tr>
                                    {header.map((cell, index) => (
                                        <th key={index} className="border-r border-zinc-700 px-3 py-2 font-semibold last:border-r-0">
                                            {cell}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                        )}
                        <tbody>
                            {table.rows.map((row, rowIndex) => (
                                <tr key={rowIndex} className="border-t border-zinc-300 odd:bg-zinc-50">
                                    {row.map((cell, cellIndex) => (
                                        <td
                                            key={cellIndex}
                                            className={`border-r border-zinc-200 px-3 py-2 align-top last:border-r-0 ${!header && rowIndex === 0 ? 'font-semibold' : ''}`}
                                        >
                                            {cell}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    )
}

/** Equations as cut from the page (PDF text of formulas is unreliable), with the extracted text as alt/title. */
export function FormulaStrip({ formulas, source }: { formulas: Formula[]; source: string }) {
    const { openLightbox } = useViewer()
    if (formulas.length === 0) return null
    const items: LightboxItem[] = formulas.map((formula) => ({
        kind: 'image', src: formula.image, title: 'Equation', caption: formula.text, source,
    }))
    return (
        <div>
            <p className="mb-2 text-xs uppercase tracking-[0.16em] text-zinc-500">Equations</p>
            <div className="flex flex-wrap gap-2">
                {formulas.map((formula, index) => (
                    <button
                        key={index}
                        type="button"
                        title={formula.text}
                        onClick={() => openLightbox(items, index)}
                        className="border border-zinc-950 bg-white px-3 py-2 hover:bg-zinc-50"
                    >
                        <img src={formula.image} alt={formula.text} className="h-9 w-auto max-w-full md:h-11" loading="lazy" />
                    </button>
                ))}
            </div>
        </div>
    )
}

function Boxes({ rects, className }: { rects: Rect[]; className: string }) {
    return (
        <>
            {rects.map(([x0, y0, x1, y1], index) => (
                <span
                    key={index}
                    className={`pointer-events-none absolute ${className}`}
                    style={{
                        left: `${x0 * 100}%`,
                        top: `${y0 * 100}%`,
                        width: `${(x1 - x0) * 100}%`,
                        height: `${(y1 - y0) * 100}%`,
                    }}
                />
            ))}
        </>
    )
}

/** The page image with the quoted sentence (and its paragraph, lighter) marked where it is printed. */
export function PageImage({ page, alt, focus = false }: { page: PageSnapshot; alt: string; focus?: boolean }) {
    // When focusing, scroll the image so the highlight sits near the top third of the frame.
    const frame = (0.75 * page.width) / page.height // visible share of the page height in a 4:3 frame
    const top = page.highlights.length ? Math.min(Math.max(0, page.highlights[0][1] - 0.12), Math.max(0, 1 - frame)) : 0
    return (
        <div className={focus ? 'relative overflow-hidden' : 'relative'} style={focus ? { aspectRatio: '4 / 3' } : undefined}>
            <div className="relative" style={focus ? { transform: `translateY(-${top * 100}%)` } : undefined}>
                <img src={page.image} alt={alt} loading="lazy" className="block w-full" width={page.width} height={page.height} />
                <Boxes rects={page.context} className="bg-amber-200/30 mix-blend-multiply" />
                <Boxes rects={page.highlights} className="bg-amber-300/60 outline outline-2 outline-amber-500 mix-blend-multiply" />
            </div>
        </div>
    )
}

type PageCardProps = { page: PageSnapshot; title: string; source: string; pageNumber: number; focus?: boolean }

export function PageCard({ page, title, source, pageNumber, focus = true }: PageCardProps) {
    const { openLightbox } = useViewer()
    const label = page.scanned ? 'Scanned page' : page.highlights.length ? 'Where it is on the page' : 'The page'
    return (
        <div className="border-2 border-zinc-950 bg-white">
            <div className="flex items-center justify-between border-b-2 border-zinc-950 px-3 py-2 text-xs uppercase tracking-[0.16em] text-zinc-500">
                <span>{label}</span>
                <span>Page {pageNumber}</span>
            </div>
            <button
                type="button"
                onClick={() => openLightbox([{ kind: 'page', page, title, source }], 0)}
                className="block w-full bg-zinc-100"
                aria-label={`Enlarge page ${pageNumber}`}
            >
                <PageImage page={page} alt={`${title}, page ${pageNumber}`} focus={focus} />
            </button>
        </div>
    )
}

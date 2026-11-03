import { useEffect } from 'react'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import type { LightboxItem } from '../lib/viewer'
import { PageImage } from './blocks/MediaBlocks'

type LightboxProps = {
    items: LightboxItem[]
    index: number
    onIndex: (index: number) => void
    onClose: () => void
}

export function Lightbox({ items, index, onIndex, onClose }: LightboxProps) {
    const item = items[index]
    const count = items.length

    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onClose()
            if (event.key === 'ArrowRight' && count > 1) onIndex((index + 1) % count)
            if (event.key === 'ArrowLeft' && count > 1) onIndex((index - 1 + count) % count)
        }
        window.addEventListener('keydown', onKey)
        const overflow = document.body.style.overflow
        document.body.style.overflow = 'hidden'
        return () => {
            window.removeEventListener('keydown', onKey)
            document.body.style.overflow = overflow
        }
    }, [index, count, onClose, onIndex])

    if (!item) return null

    return (
        <div
            className="fixed inset-0 z-50 flex flex-col bg-zinc-950/90 p-3 md:p-6"
            role="dialog"
            aria-modal="true"
            aria-label={item.title}
            onClick={onClose}
        >
            <div className="mx-auto flex w-full max-w-6xl items-start justify-between gap-4 pb-3 text-white" onClick={(event) => event.stopPropagation()}>
                <div className="min-w-0">
                    <p className="text-xs uppercase tracking-[0.18em] text-zinc-400">
                        {item.source}
                        {count > 1 && ` · ${index + 1} / ${count}`}
                    </p>
                    <p className="mt-1 text-base font-semibold">{item.title}</p>
                    {item.kind === 'image' && item.caption && <p className="mt-1 max-w-3xl text-sm text-zinc-300">{item.caption}</p>}
                </div>
                <button type="button" onClick={onClose} className="shrink-0 border-2 border-white p-1.5 hover:bg-white hover:text-zinc-950" aria-label="Close">
                    <X className="h-5 w-5" />
                </button>
            </div>

            <div className="relative mx-auto flex min-h-0 w-full max-w-6xl flex-1 items-center justify-center" onClick={(event) => event.stopPropagation()}>
                {count > 1 && (
                    <button
                        type="button"
                        onClick={() => onIndex((index - 1 + count) % count)}
                        className="absolute left-0 z-10 border-2 border-white bg-zinc-950 p-2 text-white hover:bg-white hover:text-zinc-950"
                        aria-label="Previous"
                    >
                        <ChevronLeft className="h-5 w-5" />
                    </button>
                )}
                <div className="h-full w-full overflow-auto bg-white">
                    {item.kind === 'image' ? (
                        <div className="flex min-h-full items-center justify-center p-4">
                            <img src={item.src} alt={item.caption ?? item.title} className="h-[72vh] w-auto max-w-full object-contain" />
                        </div>
                    ) : (
                        <div className="mx-auto max-w-3xl">
                            <PageImage page={item.page} alt={item.title} />
                        </div>
                    )}
                </div>
                {count > 1 && (
                    <button
                        type="button"
                        onClick={() => onIndex((index + 1) % count)}
                        className="absolute right-0 z-10 border-2 border-white bg-zinc-950 p-2 text-white hover:bg-white hover:text-zinc-950"
                        aria-label="Next"
                    >
                        <ChevronRight className="h-5 w-5" />
                    </button>
                )}
            </div>
        </div>
    )
}

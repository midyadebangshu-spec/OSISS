import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode, TouchEvent as ReactTouchEvent } from 'react'
import { createPortal } from 'react-dom'
import { Copy, FileImage, Images, Type } from 'lucide-react'
import type { CardContent } from '../lib/flashcardText'
import { cardText } from '../lib/flashcardText'
import { canvasToBlob, copyImage, renderCard, saveCard } from '../lib/flashcardRender'

type MenuItem = { label: string; icon: ReactNode; run: () => Promise<string> }

const LONG_PRESS_MS = 550

function selectionInside(element: HTMLElement | null): boolean {
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed || !selection.toString().trim() || !element) return false
    return element.contains(selection.anchorNode) || element.contains(selection.focusNode)
}

/**
 * Wraps a result so right-clicking it (or Shift+F10 / the menu key when focused, or a long press on a touch screen)
 * offers flashcard options. With text selected inside it, the browser's own menu appears instead, so copying works.
 */
export function FlashcardTarget({ getCard, children, className = '' }: { getCard: () => CardContent; children: ReactNode; className?: string }) {
    const box = useRef<HTMLDivElement>(null)
    const menuRef = useRef<HTMLDivElement>(null)
    const pressTimer = useRef<number | null>(null)
    const pressStart = useRef<{ x: number; y: number } | null>(null)
    const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
    const [status, setStatus] = useState<string | null>(null)

    const close = useCallback((restoreFocus = false) => {
        setMenu(null)
        if (restoreFocus) box.current?.focus()
    }, [])

    const open = (x: number, y: number) => {
        // Keep the menu inside the window.
        const width = 280
        const height = 250
        setMenu({ x: Math.min(x, window.innerWidth - width - 8), y: Math.min(y, window.innerHeight - height - 8) })
    }

    useEffect(() => {
        if (!menu) return
        menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
        const onDown = (event: MouseEvent) => {
            if (!menuRef.current?.contains(event.target as Node)) close()
        }
        const onScroll = () => close()
        window.addEventListener('mousedown', onDown)
        window.addEventListener('scroll', onScroll, true)
        window.addEventListener('resize', onScroll)
        return () => {
            window.removeEventListener('mousedown', onDown)
            window.removeEventListener('scroll', onScroll, true)
            window.removeEventListener('resize', onScroll)
        }
    }, [menu, close])

    useEffect(() => {
        if (!status || status === 'Making the flashcard…') return
        const timer = window.setTimeout(() => setStatus(null), 2600)
        return () => window.clearTimeout(timer)
    }, [status])

    const items: MenuItem[] = [
        { label: 'Save flashcard as PNG', icon: <FileImage className="h-4 w-4" />, run: async () => `Saved ${await saveCard(getCard(), 'image/png')}` },
        { label: 'Save flashcard as JPG', icon: <FileImage className="h-4 w-4" />, run: async () => `Saved ${await saveCard(getCard(), 'image/jpeg')}` },
        {
            label: 'Copy flashcard image',
            icon: <Copy className="h-4 w-4" />,
            run: async () => {
                await copyImage(await canvasToBlob(await renderCard(getCard()), 'image/png'))
                return 'Flashcard copied'
            },
        },
        {
            label: 'Save front and back separately',
            icon: <Images className="h-4 w-4" />,
            run: async () => {
                const card = getCard()
                const front = await saveCard(card, 'image/png', 'front')
                const back = await saveCard(card, 'image/png', 'back')
                return `Saved ${front} and ${back}`
            },
        },
        {
            label: 'Copy text',
            icon: <Type className="h-4 w-4" />,
            run: async () => {
                await navigator.clipboard.writeText(cardText(getCard()))
                return 'Text copied'
            },
        },
    ]

    const choose = async (item: MenuItem) => {
        close(true)
        setStatus('Making the flashcard…')
        try {
            setStatus(await item.run())
        } catch (reason) {
            setStatus(reason instanceof Error ? reason.message : 'Could not make the flashcard.')
        }
    }

    const onContextMenu = (event: ReactMouseEvent) => {
        if (selectionInside(box.current)) return // let the browser offer Copy
        event.preventDefault()
        open(event.clientX, event.clientY)
    }

    const onKeyDown = (event: ReactKeyboardEvent) => {
        if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
            event.preventDefault()
            const rect = box.current?.getBoundingClientRect()
            open((rect?.left ?? 0) + 24, Math.max(8, rect?.top ?? 0) + 24)
        }
    }

    const cancelPress = () => {
        if (pressTimer.current !== null) window.clearTimeout(pressTimer.current)
        pressTimer.current = null
    }
    const onTouchStart = (event: ReactTouchEvent) => {
        const touch = event.touches[0]
        pressStart.current = { x: touch.clientX, y: touch.clientY }
        cancelPress()
        pressTimer.current = window.setTimeout(() => {
            if (!selectionInside(box.current)) open(touch.clientX, touch.clientY)
        }, LONG_PRESS_MS)
    }
    const onTouchMove = (event: ReactTouchEvent) => {
        const touch = event.touches[0]
        const start = pressStart.current
        if (start && Math.hypot(touch.clientX - start.x, touch.clientY - start.y) > 10) cancelPress()
    }

    const onMenuKey = (event: ReactKeyboardEvent) => {
        const buttons = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])]
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
        if (event.key === 'Escape' || event.key === 'Tab') {
            event.preventDefault()
            close(true)
        } else if (event.key === 'ArrowDown') {
            event.preventDefault()
            buttons[(index + 1) % buttons.length]?.focus()
        } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            buttons[(index - 1 + buttons.length) % buttons.length]?.focus()
        }
    }

    return (
        <div
            ref={box}
            tabIndex={0}
            className={`outline-none focus-visible:ring-2 focus-visible:ring-amber-500 ${className}`}
            onContextMenu={onContextMenu}
            onKeyDown={onKeyDown}
            onTouchStart={onTouchStart}
            onTouchMove={onTouchMove}
            onTouchEnd={cancelPress}
            onTouchCancel={cancelPress}
        >
            <span className="sr-only">Right-click, long-press or press Shift+F10 for flashcard options.</span>
            {children}
            {menu &&
                createPortal(
                    <div
                        ref={menuRef}
                        role="menu"
                        aria-label="Flashcard"
                        onKeyDown={onMenuKey}
                        className="fixed z-50 w-[17.5rem] border-2 border-zinc-950 bg-white py-1 shadow-[4px_4px_0_0_#18181b]"
                        style={{ left: menu.x, top: menu.y }}
                    >
                        <p className="px-3 pb-1 pt-1.5 text-[11px] uppercase tracking-[0.16em] text-zinc-500">Flashcard</p>
                        {items.map((item, index) => (
                            <button
                                key={item.label}
                                type="button"
                                role="menuitem"
                                onClick={() => choose(item)}
                                className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-zinc-100 focus:bg-zinc-100 focus:outline-none ${
                                    index === items.length - 1 ? 'border-t border-zinc-200' : ''
                                }`}
                            >
                                {item.icon}
                                {item.label}
                            </button>
                        ))}
                    </div>,
                    document.body,
                )}
            {status &&
                createPortal(
                    <div role="status" aria-live="polite" className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm text-white">
                        {status}
                    </div>,
                    document.body,
                )}
        </div>
    )
}

import type { BoardRelation } from '../types/workspace.ts'

export const NODE_WIDTH = 230
export const DEFAULT_HEIGHT = 96

export type Box = { x: number; y: number; width: number; height: number }

export const RELATION_STYLE: Record<BoardRelation, { color: string; label: string; dash?: string }> = {
    supports: { color: '#16a34a', label: 'supports' },
    contradicts: { color: '#dc2626', label: 'contradicts' },
    explains: { color: '#2563eb', label: 'explains' },
    relates: { color: '#71717a', label: 'relates to', dash: '5 4' },
}

export const RELATIONS: BoardRelation[] = ['supports', 'contradicts', 'explains', 'relates']

function centre(box: Box): [number, number] {
    return [box.x + box.width / 2, box.y + box.height / 2]
}

/** Where the line between two boxes leaves the first and enters the second (their borders, not their centres). */
export function edgeEndpoints(from: Box, to: Box): { x1: number; y1: number; x2: number; y2: number } {
    const [ax, ay] = centre(from)
    const [bx, by] = centre(to)
    const border = (box: Box, cx: number, cy: number, dx: number, dy: number): [number, number] => {
        if (dx === 0 && dy === 0) return [cx, cy]
        const scale = Math.min(dx === 0 ? Infinity : box.width / 2 / Math.abs(dx), dy === 0 ? Infinity : box.height / 2 / Math.abs(dy))
        return [cx + dx * scale, cy + dy * scale]
    }
    const [x1, y1] = border(from, ax, ay, bx - ax, by - ay)
    const [x2, y2] = border(to, bx, by, ax - bx, ay - by)
    return { x1, y1, x2, y2 }
}

/** The view that shows every box with some margin: [translateX, translateY, scale], for a viewport of the given size. */
export function fitView(boxes: Box[], viewWidth: number, viewHeight: number, margin = 40): { x: number; y: number; scale: number } {
    if (boxes.length === 0) return { x: viewWidth / 2 - NODE_WIDTH / 2, y: 60, scale: 1 }
    const left = Math.min(...boxes.map((b) => b.x))
    const top = Math.min(...boxes.map((b) => b.y))
    const right = Math.max(...boxes.map((b) => b.x + b.width))
    const bottom = Math.max(...boxes.map((b) => b.y + b.height))
    const scale = Math.min(1, (viewWidth - 2 * margin) / (right - left), (viewHeight - 2 * margin) / (bottom - top))
    return { scale, x: margin - left * scale + (viewWidth - 2 * margin - (right - left) * scale) / 2, y: margin - top * scale }
}

/** A screen point as board coordinates, for a view {x, y, scale}. */
export function toBoard(view: { x: number; y: number; scale: number }, screenX: number, screenY: number): [number, number] {
    return [(screenX - view.x) / view.scale, (screenY - view.y) / view.scale]
}

export function clampScale(scale: number): number {
    return Math.min(2.5, Math.max(0.2, scale))
}

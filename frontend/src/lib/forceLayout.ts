/**
 * A small deterministic force-directed layout for the concept graph (no dependencies): node repulsion,
 * springs on links, a pull towards the centre, collision padding, and a fixed centre node. Runs a fixed number
 * of iterations synchronously, so the same graph always gets the same picture.
 */

export type LayoutNode = { id: number; radius: number; fixed?: boolean; x?: number; y?: number }
export type LayoutEdge = { source: number; target: number; length: number; strength: number }
export type Positioned = { id: number; x: number; y: number }

export function forceLayout(
    nodes: LayoutNode[],
    edges: LayoutEdge[],
    width: number,
    height: number,
    { iterations = 320, padding = 70 }: { iterations?: number; padding?: number } = {},
): Map<number, Positioned> {
    const cx = width / 2
    const cy = height / 2
    const points = nodes.map((node, index) => {
        if (node.fixed) return { ...node, x: cx, y: cy, vx: 0, vy: 0 }
        // Start on a spiral: deterministic and already spread out.
        const angle = index * 2.399963
        const distance = 40 + 14 * index
        return { ...node, x: cx + Math.cos(angle) * distance, y: cy + Math.sin(angle) * distance, vx: 0, vy: 0 }
    })
    const index = new Map(points.map((point, i) => [point.id, i]))

    for (let step = 0; step < iterations; step++) {
        const cooling = 1 - step / iterations
        // Repulsion between every pair (graphs here have at most ~25 nodes).
        for (let i = 0; i < points.length; i++) {
            for (let j = i + 1; j < points.length; j++) {
                const a = points[i]
                const b = points[j]
                let dx = b.x - a.x
                let dy = b.y - a.y
                let distance = Math.hypot(dx, dy)
                if (distance < 0.01) {
                    dx = 0.01 * (i - j)
                    dy = 0.01
                    distance = 0.02
                }
                const minimum = a.radius + b.radius + 26
                const force = 5200 / (distance * distance) + (distance < minimum ? (minimum - distance) * 0.6 : 0)
                const fx = (dx / distance) * force
                const fy = (dy / distance) * force
                a.vx -= fx
                a.vy -= fy
                b.vx += fx
                b.vy += fy
            }
        }
        // Springs.
        for (const edge of edges) {
            const a = points[index.get(edge.source) ?? -1]
            const b = points[index.get(edge.target) ?? -1]
            if (!a || !b) continue
            const dx = b.x - a.x
            const dy = b.y - a.y
            const distance = Math.max(0.01, Math.hypot(dx, dy))
            const force = (distance - edge.length) * 0.04 * edge.strength
            const fx = (dx / distance) * force
            const fy = (dy / distance) * force
            a.vx += fx
            a.vy += fy
            b.vx -= fx
            b.vy -= fy
        }
        // Gravity, integration, bounds.
        for (const point of points) {
            if (point.fixed) {
                point.x = cx
                point.y = cy
                point.vx = point.vy = 0
                continue
            }
            point.vx += (cx - point.x) * 0.006
            point.vy += (cy - point.y) * 0.006
            const speed = Math.hypot(point.vx, point.vy)
            const limit = 24 * cooling + 1
            if (speed > limit) {
                point.vx = (point.vx / speed) * limit
                point.vy = (point.vy / speed) * limit
            }
            point.x = Math.min(width - padding, Math.max(padding, point.x + point.vx))
            point.y = Math.min(height - 30, Math.max(24, point.y + point.vy))
            point.vx *= 0.55
            point.vy *= 0.55
        }
    }
    return new Map(points.map((point) => [point.id, { id: point.id, x: point.x, y: point.y }]))
}

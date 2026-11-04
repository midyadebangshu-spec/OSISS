import assert from 'node:assert/strict'
import test from 'node:test'
import { clampScale, edgeEndpoints, fitView, toBoard } from '../src/lib/board.ts'

const a = { x: 0, y: 0, width: 200, height: 100 }
const b = { x: 400, y: 0, width: 200, height: 100 }

test('edgeEndpoints leaves and enters at the borders', () => {
    const line = edgeEndpoints(a, b)
    assert.deepEqual(line, { x1: 200, y1: 50, x2: 400, y2: 50 }) // side by side: right edge to left edge
    const down = edgeEndpoints(a, { x: 0, y: 300, width: 200, height: 100 })
    assert.deepEqual(down, { x1: 100, y1: 100, x2: 100, y2: 300 })
    const diagonal = edgeEndpoints(a, { x: 400, y: 400, width: 200, height: 100 })
    assert.ok(diagonal.x1 > 100 && diagonal.x1 <= 200 && diagonal.y1 > 50 && diagonal.y1 <= 100)
    assert.deepEqual(edgeEndpoints(a, a), { x1: 100, y1: 50, x2: 100, y2: 50 }) // the same box: no line
})

test('fitView shows every box inside the viewport', () => {
    const view = fitView([a, b], 1000, 600)
    assert.equal(view.scale, 1) // already fits: no zoom in beyond 100%
    const wide = fitView([a, { x: 3000, y: 0, width: 200, height: 100 }], 800, 600)
    assert.ok(wide.scale < 0.25)
    assert.ok(wide.x + 0 * wide.scale >= 0 && wide.x + 3200 * wide.scale <= 800 + 1e-6)
    assert.equal(fitView([], 800, 600).scale, 1)
})

test('toBoard inverts the view transform', () => {
    const view = { x: 100, y: 50, scale: 2 }
    assert.deepEqual(toBoard(view, 300, 250), [100, 100])
})

test('clampScale keeps zoom sensible', () => {
    assert.equal(clampScale(10), 2.5)
    assert.equal(clampScale(0.01), 0.2)
    assert.equal(clampScale(1), 1)
})

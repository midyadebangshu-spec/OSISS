import type { Neighbour, Relation } from '../types/concepts'

const PHRASES: Record<Relation, { out: string; in: string }> = {
    is_a: { out: 'is a type of', in: 'has a type' },
    has_part: { out: 'consists of', in: 'is part of' },
    used_for: { out: 'is used for', in: 'uses' },
    causes: { out: 'causes', in: 'is caused by' },
    contrast: { out: 'contrasts with', in: 'contrasts with' },
}

export const RELATION_COLOR: Record<Relation, string> = {
    is_a: '#2563eb',
    has_part: '#16a34a',
    used_for: '#9333ea',
    causes: '#dc2626',
    contrast: '#d97706',
}

/** "stack — is a type of — data structure": how the open concept relates to a neighbour. */
export function relationPhrase(neighbour: Pick<Neighbour, 'relation' | 'direction'>): string {
    if (!neighbour.relation) return 'appears with'
    const phrase = PHRASES[neighbour.relation]
    return neighbour.direction === 'in' ? phrase.in : phrase.out
}

/** Teaching-order hint from how many books reach the open concept first. */
export function orderPhrase(order: number): string | null {
    if (order >= 2) return 'usually taught after it'
    if (order <= -2) return 'usually taught before it'
    return null
}

import type { Figure, SearchResult } from '../types/search'
import type { LightboxItem } from './viewer'

export const KIND_LABEL: Record<SearchResult['kind'], string> = {
    definition: 'Definition',
    list: 'List',
    code: 'Code',
    figure: 'Figure',
    table: 'Table',
    text: 'Passage',
}

export function sourceLabel(result: SearchResult): string {
    return `${result.book_title} · p. ${result.page_number}`
}

/** Whether a hit has a structured block (definition / list / code) to lead with. */
export function hasLead(result: SearchResult): boolean {
    return result.blocks.length > 0
}

export function figureTitle(item: { label?: string | null; page_number: number }, noun = 'Figure'): string {
    return item.label ? `${noun} ${item.label}` : `${noun} on page ${item.page_number}`
}

export function figureLightboxItem(figure: Figure, source: string, noun = 'Figure'): LightboxItem {
    return { kind: 'image', src: figure.image, title: figureTitle(figure, noun), caption: figure.caption, source }
}

export function figureGallery(result: SearchResult): LightboxItem[] {
    return result.figures.map((figure) => figureLightboxItem(figure, sourceLabel(result)))
}

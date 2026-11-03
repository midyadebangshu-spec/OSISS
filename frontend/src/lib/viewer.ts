import { createContext, useContext } from 'react'
import type { PagePreview, PageSnapshot } from '../types/search'

/** Something the lightbox can show: a figure / table / formula crop, or a page image with highlights. */
export type LightboxItem =
    | { kind: 'image'; src: string; title: string; caption?: string | null; source: string }
    | { kind: 'page'; page: PageSnapshot; title: string; source: string }

export type Viewer = {
    openLightbox: (items: LightboxItem[], index: number) => void
    togglePage: (preview: PagePreview) => void
    isPageOpen: (pdfLink: string, pageNumber: number) => boolean
}

export const ViewerContext = createContext<Viewer>({
    openLightbox: () => undefined,
    togglePage: () => undefined,
    isPageOpen: () => false,
})

export function useViewer(): Viewer {
    return useContext(ViewerContext)
}

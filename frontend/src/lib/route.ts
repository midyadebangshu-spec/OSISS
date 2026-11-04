import { useEffect, useState } from 'react'

/** Pages of the site, kept in the URL hash so links can be shared: #/ (search), #/explore[/<id>], #/library[/<id>]. */
export type Route =
    | { page: 'search' }
    | { page: 'explore'; conceptId: number | null }
    | { page: 'library'; bookId: number | null; readPage: number | null }
    | { page: 'account' }
    | { page: 'workspace'; tab: WorkspaceTab; id: number | null }

export type WorkspaceTab = 'dashboard' | 'projects' | 'collections' | 'notes'

export function parseHash(hash: string): Route {
    const explore = /^#\/explore(?:\/(\d+))?\/?$/.exec(hash)
    if (explore) return { page: 'explore', conceptId: explore[1] ? Number(explore[1]) : null }
    const library = /^#\/library(?:\/(\d+)(?:\/read(?:\/(\d+))?)?)?\/?$/.exec(hash)
    if (library) {
        const reading = /\/read/.test(hash)
        return {
            page: 'library',
            bookId: library[1] ? Number(library[1]) : null,
            readPage: reading ? Number(library[2] ?? 1) : null,
        }
    }
    if (/^#\/account\/?$/.test(hash)) return { page: 'account' }
    const workspace = /^#\/workspace(?:\/(projects|collections|notes)(?:\/(\d+))?)?\/?$/.exec(hash)
    if (workspace) {
        return { page: 'workspace', tab: (workspace[1] as WorkspaceTab | undefined) ?? 'dashboard', id: workspace[2] ? Number(workspace[2]) : null }
    }
    return { page: 'search' }
}

export function workspaceHref(tab: WorkspaceTab = 'dashboard', id?: number | null): string {
    if (tab === 'dashboard') return '#/workspace'
    return id ? `#/workspace/${tab}/${id}` : `#/workspace/${tab}`
}

export function libraryHref(bookId?: number | null): string {
    return bookId ? `#/library/${bookId}` : '#/library'
}

/** The reader, opened at a PDF page (1 when omitted). */
export function readerHref(bookId: number, page = 1): string {
    return `#/library/${bookId}/read/${page}`
}

export function exploreHref(conceptId?: number | null): string {
    return conceptId ? `#/explore/${conceptId}` : '#/explore'
}

export function useRoute(): Route {
    const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
    useEffect(() => {
        const onChange = () => setRoute(parseHash(window.location.hash))
        window.addEventListener('hashchange', onChange)
        return () => window.removeEventListener('hashchange', onChange)
    }, [])
    return route
}

export function navigate(href: string): void {
    window.location.hash = href
}

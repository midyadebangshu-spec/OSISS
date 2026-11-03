import { useEffect, useState } from 'react'

/** Pages of the site, kept in the URL hash so links can be shared: #/ (search), #/explore, #/explore/<id>. */
export type Route = { page: 'search' } | { page: 'explore'; conceptId: number | null }

export function parseHash(hash: string): Route {
    const match = /^#\/explore(?:\/(\d+))?\/?$/.exec(hash)
    if (match) return { page: 'explore', conceptId: match[1] ? Number(match[1]) : null }
    return { page: 'search' }
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

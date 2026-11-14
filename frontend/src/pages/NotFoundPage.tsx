import { Briefcase, Library, Network, Search } from 'lucide-react'
import { routeBase } from '../lib/route'

const LINK =
    'inline-flex items-center gap-2 border-2 border-zinc-950 px-4 py-2 text-sm font-medium hover:bg-zinc-950 hover:text-white'

/** Shown for an address the site does not know: an unrecognised "#/..." route, or a path other than "/". */
export function NotFoundPage({ address }: { address: string }) {
    const base = routeBase()
    return (
        <section className="mx-auto max-w-2xl py-10 md:py-16" aria-labelledby="notfound-title">
            <p className="text-[clamp(5.5rem,22vw,10rem)] font-bold leading-none tracking-tighter" aria-hidden="true">
                4<mark className="inline-block bg-amber-400 px-1 pb-1 pt-2 leading-[0.8] text-zinc-950">0</mark>4
            </p>
            <p className="mt-5 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">Page not found</p>
            <h1 id="notfound-title" className="mt-2 text-3xl font-bold tracking-tight md:text-4xl">
                This page isn’t in the book.
            </h1>
            <p className="mt-3 text-zinc-600">
                The address may have a typo, or the page may have moved. OSISS only shows text that exists in a source, so
                rather than guess, here is the way back.
            </p>
            <blockquote className="mt-6 border-2 border-l-8 border-zinc-950 bg-zinc-50 px-4 py-3 text-zinc-700">
                “No results on this page.”
                <span className="mt-1 block text-sm text-zinc-500">OSISS, page 404</span>
            </blockquote>
            <div className="mt-6 flex flex-wrap gap-3">
                <a href={`${base}#/`} className={`${LINK} bg-zinc-950 text-white hover:bg-zinc-800`}>
                    <Search className="h-4 w-4" />
                    Search
                </a>
                <a href={`${base}#/library`} className={LINK}>
                    <Library className="h-4 w-4" />
                    Library
                </a>
                <a href={`${base}#/explore`} className={LINK}>
                    <Network className="h-4 w-4" />
                    Explore
                </a>
                <a href={`${base}#/workspace`} className={LINK}>
                    <Briefcase className="h-4 w-4" />
                    Workspace
                </a>
            </div>
            <p className="mt-6 break-all text-sm text-zinc-500">
                You asked for <code className="border border-zinc-300 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs">{address}</code>
            </p>
        </section>
    )
}

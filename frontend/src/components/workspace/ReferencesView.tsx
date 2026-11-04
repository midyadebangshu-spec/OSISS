import { useEffect, useState } from 'react'
import { Copy, Download } from 'lucide-react'
import { getReferences, listProjects, referencesFileUrl } from '../../lib/workspaceApi'
import { libraryHref } from '../../lib/route'
import type { CitationStyle, Project, References } from '../../types/workspace'
import { RichText } from './RichText'

const STYLES: { value: CitationStyle; label: string }[] = [
    { value: 'apa', label: 'APA 7' },
    { value: 'mla', label: 'MLA 9' },
    { value: 'chicago', label: 'Chicago (author-date)' },
    { value: 'ieee', label: 'IEEE' },
]
const SELECT = 'border-2 border-zinc-950 bg-white px-2 py-1.5 text-sm'

/** The books the scholar saved from or put in a project, formatted in a citation style, with BibTeX / RIS download. */
export function ReferencesView() {
    const [style, setStyle] = useState<CitationStyle>('apa')
    const [projectId, setProjectId] = useState<number | undefined>(undefined)
    const [projects, setProjects] = useState<Project[]>([])
    const [data, setData] = useState<References | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [copied, setCopied] = useState<string | null>(null)

    useEffect(() => {
        listProjects().then(setProjects).catch(() => undefined)
    }, [])

    useEffect(() => {
        const controller = new AbortController()
        getReferences(style, projectId, controller.signal)
            .then((result) => {
                setData(result)
                setError(null)
            })
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load the references.')
            })
        return () => controller.abort()
    }, [style, projectId])

    const copy = async (text: string, label: string) => {
        try {
            await navigator.clipboard.writeText(text)
            setCopied(label)
        } catch {
            setCopied('Copying is not allowed in this browser.')
        }
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-2">Style
                    <select className={SELECT} value={style} onChange={(event) => setStyle(event.target.value as CitationStyle)}>
                        {STYLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                </label>
                <label className="flex items-center gap-2">Project
                    <select className={SELECT} value={projectId ?? ''} onChange={(event) => setProjectId(event.target.value ? Number(event.target.value) : undefined)}>
                        <option value="">All my books</option>
                        {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
                    </select>
                </label>
                <span className="ml-auto flex flex-wrap gap-4">
                    <a href={referencesFileUrl('bibtex', projectId)} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Download className="h-4 w-4" /> BibTeX</a>
                    <a href={referencesFileUrl('ris', projectId)} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Download className="h-4 w-4" /> RIS</a>
                    {data && data.entries.length > 0 && (
                        <button type="button" onClick={() => void copy(data.entries.map((entry) => entry.plain).join('\n\n'), 'All references copied.')} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Copy className="h-4 w-4" /> Copy all</button>
                    )}
                </span>
            </div>
            {copied && <p role="status" className="text-sm text-zinc-600">{copied}</p>}
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {!data ? (
                !error && <p className="text-sm text-zinc-500">Loading…</p>
            ) : data.entries.length === 0 ? (
                <p className="text-sm text-zinc-600">No references yet. Books appear here once you save a passage from them or add them to a project.</p>
            ) : (
                <ol className="space-y-3">
                    {data.entries.map((entry) => (
                        <li key={entry.book_id} className="space-y-1 border-2 border-zinc-950 p-3">
                            <p className="leading-relaxed"><RichText text={entry.marked} /></p>
                            {entry.warnings.length > 0 && (
                                <ul className="text-xs text-amber-800">
                                    {entry.warnings.map((warning) => <li key={warning}>⚠ {warning}</li>)}
                                </ul>
                            )}
                            <p className="flex gap-4 text-xs">
                                <button type="button" onClick={() => void copy(entry.plain, 'Reference copied.')} className="underline-offset-4 hover:underline">Copy</button>
                                <a href={libraryHref(entry.book_id)} className="underline-offset-4 hover:underline">Open the book</a>
                            </p>
                        </li>
                    ))}
                </ol>
            )}
            <p className="text-xs text-zinc-500">Missing details (year, publisher, printed page numbers) are set by the administrator in the admin app. Entries are books only.</p>
        </div>
    )
}

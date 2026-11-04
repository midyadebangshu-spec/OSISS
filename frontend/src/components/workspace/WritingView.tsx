import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, Check, Download, FilePlus2, Printer, ShieldCheck, Trash2 } from 'lucide-react'
import { citationSnippet, insertAt } from '../../lib/richText'
import { readerHref, workspaceHref } from '../../lib/route'
import {
    checkDraft, createDraft, deleteDraft, draftExportUrl, getDraft, listDrafts, listEvidence, listProjects, renderDraft, updateDraft,
} from '../../lib/workspaceApi'
import type { CitationStyle, DraftCheck, DraftSummary, Evidence, Project, QuoteCheck, QuoteStatus, RenderedDraft } from '../../types/workspace'
import { RichText } from './RichText'

const FIELD = 'border-2 border-zinc-950 px-3 py-2 text-sm outline-none focus:bg-zinc-50'
const PRIMARY = 'inline-flex items-center gap-1 border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white disabled:opacity-40'
const STYLES: { value: CitationStyle; label: string }[] = [
    { value: 'apa', label: 'APA 7' }, { value: 'mla', label: 'MLA 9' }, { value: 'chicago', label: 'Chicago (author-date)' }, { value: 'ieee', label: 'IEEE' },
]

function message(error: unknown): string {
    return error instanceof Error ? error.message : 'Something went wrong.'
}

const STATUS: Record<QuoteStatus, { label: string; tone: string }> = {
    verbatim: { label: 'Matches the page', tone: 'text-green-800' },
    other_page: { label: 'Found on a different page', tone: 'text-amber-800' },
    partial: { label: 'Differs from the page', tone: 'text-red-700' },
    not_found: { label: 'Not found on the page', tone: 'text-red-700' },
    uncited: { label: 'No citation', tone: 'text-red-700' },
    unknown_source: { label: 'Cited source not found', tone: 'text-red-700' },
    unchecked: { label: 'Page text unavailable', tone: 'text-amber-800' },
}

function QuoteResult({ result, evidence }: { result: QuoteCheck; evidence: Map<number, Evidence> }) {
    const info = STATUS[result.status]
    const item = result.evidence_id ? evidence.get(result.evidence_id) : undefined
    const ok = result.status === 'verbatim'
    return (
        <li className="space-y-1 border border-zinc-300 p-2 text-sm">
            <p className={`flex items-center gap-1 font-medium ${info.tone}`}>
                {ok ? <Check className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />} {info.label}
                <span className="font-normal text-zinc-500"> · line {result.line}</span>
            </p>
            <p className="line-clamp-3 text-zinc-700">“{result.text}”</p>
            {result.status === 'other_page' && <p className="text-xs text-zinc-600">It is on PDF page {result.found_on_page}; you cite page {result.page}.</p>}
            {(result.status === 'partial' || result.status === 'not_found') && (
                <>
                    {result.matched !== undefined && <p className="text-xs text-zinc-600">{Math.round(result.matched * 100)}% of your words match the page.</p>}
                    {result.closest && <p className="border-l-2 border-zinc-400 pl-2 text-xs text-zinc-700">The page says: “{result.closest}”</p>}
                </>
            )}
            {result.status === 'unchecked' && <p className="text-xs text-zinc-600">{result.matches_saved_quote ? 'It matches the passage you saved.' : 'It does not match the passage you saved either.'}</p>}
            {result.status === 'uncited' && <p className="text-xs text-zinc-600">Add a citation with a saved passage, or remove the quotation marks.</p>}
            {item?.book_id && item.page_number && (
                <a href={readerHref(item.book_id, item.page_number)} target="_blank" rel="noreferrer" className="text-xs underline underline-offset-4">Open the cited page</a>
            )}
        </li>
    )
}

function Editor({ id }: { id: number }) {
    const [title, setTitle] = useState('')
    const [body, setBody] = useState('')
    const [style, setStyle] = useState<CitationStyle>('apa')
    const [loaded, setLoaded] = useState(false)
    const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved')
    const [error, setError] = useState<string | null>(null)
    const [tab, setTab] = useState<'evidence' | 'check' | 'preview'>('evidence')
    const [evidence, setEvidence] = useState<Evidence[]>([])
    const [projects, setProjects] = useState<Project[]>([])
    const [projectId, setProjectId] = useState<number | undefined>(undefined)
    const [filter, setFilter] = useState('')
    const [check, setCheck] = useState<DraftCheck | null>(null)
    const [checking, setChecking] = useState(false)
    const [rendered, setRendered] = useState<RenderedDraft | null>(null)
    const area = useRef<HTMLTextAreaElement>(null)
    const timer = useRef<number | null>(null)
    const latest = useRef({ title: '', body: '', style: 'apa' as CitationStyle })
    const pending = useRef<Promise<unknown> | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        getDraft(id, controller.signal)
            .then((draft) => {
                setTitle(draft.title)
                setBody(draft.body)
                setStyle(draft.style)
                latest.current = { title: draft.title, body: draft.body, style: draft.style }
                setLoaded(true)
            })
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [id])

    useEffect(() => {
        listProjects().then(setProjects).catch(() => undefined)
    }, [])

    useEffect(() => {
        const controller = new AbortController()
        listEvidence({ projectId }, controller.signal)
            .then((result) => setEvidence(result.items.filter((item) => item.kind !== 'concept')))
            .catch(() => undefined)
        return () => controller.abort()
    }, [projectId])

    const save = useCallback((): Promise<unknown> => {
        const work = updateDraft(id, latest.current)
            .then(() => {
                setSaveState('saved')
                setError(null)
            })
            .catch((failure: unknown) => {
                setSaveState('error')
                setError(message(failure))
            })
        pending.current = work
        return work
    }, [id])

    /** Waits for the autosave, so an export or check sees the text as it is on screen. */
    const flush = useCallback(async () => {
        if (timer.current) {
            window.clearTimeout(timer.current)
            timer.current = null
            await save()
        } else if (pending.current) {
            await pending.current
        }
    }, [save])

    const change = (next: Partial<typeof latest.current>) => {
        latest.current = { ...latest.current, ...next }
        if (next.title !== undefined) setTitle(next.title)
        if (next.body !== undefined) setBody(next.body)
        if (next.style !== undefined) setStyle(next.style)
        setSaveState('saving')
        if (timer.current) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => {
            timer.current = null
            void save()
        }, 800)
    }

    useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

    const insert = (text: string) => {
        const element = area.current
        const start = element?.selectionStart ?? body.length
        const end = element?.selectionEnd ?? body.length
        const result = insertAt(body, start, end, text)
        change({ body: result.text })
        window.requestAnimationFrame(() => {
            element?.focus()
            element?.setSelectionRange(result.cursor, result.cursor)
        })
    }

    const runCheck = async () => {
        setChecking(true)
        setTab('check')
        try {
            await flush()
            setCheck(await checkDraft(id))
            setError(null)
        } catch (failure) {
            setError(message(failure))
        } finally {
            setChecking(false)
        }
    }

    const loadPreview = useCallback(async () => {
        await flush()
        setRendered(await renderDraft(id, latest.current.style))
    }, [flush, id])

    useEffect(() => {
        if (tab !== 'preview') return
        loadPreview().catch((failure: unknown) => setError(message(failure)))
    }, [tab, style, loadPreview])

    useEffect(() => {
        const done = () => document.documentElement.classList.remove('print-draft')
        window.addEventListener('afterprint', done)
        return () => window.removeEventListener('afterprint', done)
    }, [])

    const print = async () => {
        setTab('preview')
        try {
            await loadPreview()
        } catch (failure) {
            setError(message(failure))
            return
        }
        window.requestAnimationFrame(() => {
            document.documentElement.classList.add('print-draft')
            window.print()
        })
    }

    const download = async (format: 'docx' | 'tex' | 'md') => {
        await flush()
        window.location.href = draftExportUrl(id, format, latest.current.style)
    }

    const remove = async () => {
        if (!window.confirm('Delete this draft?')) return
        await deleteDraft(id)
        window.location.hash = workspaceHref('writing')
    }

    const evidenceById = useMemo(() => new Map(evidence.map((item) => [item.id, item])), [evidence])
    const shown = useMemo(() => {
        const needle = filter.trim().toLowerCase()
        return evidence.filter((item) => !needle || `${item.quote} ${item.book_title ?? ''}`.toLowerCase().includes(needle))
    }, [evidence, filter])

    if (error && !loaded) return <p role="alert" className="border-2 border-zinc-950 p-4 text-sm">{error}</p>
    if (!loaded) return <p className="text-sm text-zinc-500">Loading…</p>

    const TabButton = ({ value, children }: { value: typeof tab; children: React.ReactNode }) => (
        <button type="button" onClick={() => setTab(value)} aria-pressed={tab === value} className={`border-b-2 px-2 py-1 text-sm font-medium ${tab === value ? 'border-zinc-950' : 'border-transparent text-zinc-500 hover:text-zinc-950'}`}>{children}</button>
    )

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <a href={workspaceHref('writing')} className="inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"><ArrowLeft className="h-4 w-4" /> All drafts</a>
                <div className="flex flex-wrap items-center gap-4 text-sm">
                    <span role="status" className={saveState === 'error' ? 'text-red-700' : 'text-zinc-500'}>{saveState === 'saving' ? 'Saving…' : saveState === 'error' ? 'Not saved' : 'Saved'}</span>
                    <button type="button" onClick={() => void download('docx')} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Download className="h-4 w-4" /> Word</button>
                    <button type="button" onClick={() => void download('tex')} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Download className="h-4 w-4" /> LaTeX</button>
                    <button type="button" onClick={() => void download('md')} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Download className="h-4 w-4" /> Markdown</button>
                    <button type="button" onClick={() => void print()} className="inline-flex items-center gap-1 underline-offset-4 hover:underline"><Printer className="h-4 w-4" /> Print / PDF</button>
                    <button type="button" onClick={() => void remove()} className="inline-flex items-center gap-1 text-red-700 underline-offset-4 hover:underline"><Trash2 className="h-4 w-4" /> Delete</button>
                </div>
            </div>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                <div className="space-y-3">
                    <div className="flex flex-wrap gap-2">
                        <input className={`${FIELD} min-w-0 flex-1 text-lg font-semibold`} value={title} onChange={(event) => change({ title: event.target.value })} maxLength={200} placeholder="Title" aria-label="Draft title" />
                        <select className={`${FIELD} bg-white`} value={style} onChange={(event) => change({ style: event.target.value as CitationStyle })} aria-label="Citation style">
                            {STYLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                        </select>
                    </div>
                    <textarea
                        ref={area}
                        className={`${FIELD} min-h-[30rem] w-full leading-relaxed`}
                        value={body}
                        onChange={(event) => change({ body: event.target.value })}
                        aria-label="Draft text"
                        placeholder={'Write here.\n\n# Heading\n> A block quotation [@e12]\n“An inline quotation” [@e12]\nA paraphrase [@e12].\n\nInsert saved passages from the panel on the right.'}
                    />
                </div>
                <aside className="space-y-3">
                    <div className="flex items-center gap-3 border-b border-zinc-200">
                        <TabButton value="evidence">Evidence</TabButton>
                        <TabButton value="check">Check quotes{check && check.problems > 0 ? ` (${check.problems})` : ''}</TabButton>
                        <TabButton value="preview">Preview</TabButton>
                    </div>

                    {tab === 'evidence' && (
                        <div className="space-y-3">
                            <div className="flex flex-wrap gap-2">
                                <input className={`${FIELD} min-w-0 flex-1`} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter saved items" aria-label="Filter saved items" />
                                <select className={`${FIELD} bg-white`} value={projectId ?? ''} onChange={(event) => setProjectId(event.target.value ? Number(event.target.value) : undefined)} aria-label="Project">
                                    <option value="">All saved</option>
                                    {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
                                </select>
                            </div>
                            {shown.length === 0 ? (
                                <p className="text-sm text-zinc-600">Nothing saved yet. Use “Save” on search results or highlight text in the reader.</p>
                            ) : (
                                <ul className="max-h-[34rem] space-y-2 overflow-y-auto">
                                    {shown.map((item) => (
                                        <li key={item.id} className="space-y-1 border border-zinc-300 p-2 text-sm">
                                            <p className="line-clamp-4">“{item.quote || item.kind}”</p>
                                            <p className="text-xs text-zinc-500">{item.book_title}{item.page_number ? ` · p. ${item.printed_page ?? item.page_number}` : ''}</p>
                                            <p className="flex gap-3 text-xs">
                                                {item.quote && <button type="button" onClick={() => insert(citationSnippet('block', item.id, item.quote))} className="underline-offset-4 hover:underline">Insert as quote</button>}
                                                {item.quote && <button type="button" onClick={() => insert(citationSnippet('inline', item.id, item.quote))} className="underline-offset-4 hover:underline">Inline</button>}
                                                <button type="button" onClick={() => insert(citationSnippet('cite', item.id, ''))} className="underline-offset-4 hover:underline">Cite only</button>
                                            </p>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}

                    {tab === 'check' && (
                        <div className="space-y-3">
                            <p className="text-sm text-zinc-600">Compares each quotation with the text of the page it cites, ignoring line breaks, hyphenation, case and quote marks. Use […] for an omission.</p>
                            <button type="button" onClick={() => void runCheck()} disabled={checking} className={PRIMARY}><ShieldCheck className="h-4 w-4" /> {checking ? 'Checking…' : 'Check quotes'}</button>
                            {check && (
                                check.quotes.length === 0 ? (
                                    <p className="text-sm text-zinc-600">No quotations found. Block quotes start with “&gt;”; inline quotations are followed by a citation.</p>
                                ) : (
                                    <>
                                        <p role="status" className={`text-sm font-medium ${check.problems ? 'text-red-700' : 'text-green-800'}`}>
                                            {check.problems ? `${check.problems} of ${check.quotes.length} quotations need attention` : `All ${check.quotes.length} quotations match their pages`}
                                        </p>
                                        <ul className="space-y-2">{check.quotes.map((result, index) => <QuoteResult key={index} result={result} evidence={evidenceById} />)}</ul>
                                    </>
                                )
                            )}
                        </div>
                    )}

                    {tab === 'preview' && (
                        <div className="space-y-3">
                            {!rendered ? <p className="text-sm text-zinc-500">Preparing…</p> : (
                                <article id="draft-preview" className="space-y-3 border-2 border-zinc-950 bg-white p-4 font-serif text-[15px] leading-relaxed">
                                    <h1 className="text-xl font-semibold">{rendered.title}</h1>
                                    {rendered.blocks.map((block, index) =>
                                        block.type === 'heading' ? <h2 key={index} className="mt-4 text-lg font-semibold"><RichText text={block.text} /></h2>
                                        : block.type === 'quote' ? <blockquote key={index} className="ml-6 border-l-2 border-zinc-300 pl-3"><RichText text={block.text} /></blockquote>
                                        : <p key={index}><RichText text={block.text} /></p>)}
                                    {rendered.bibliography.length > 0 && (
                                        <>
                                            <h2 className="mt-6 text-lg font-semibold">{rendered.style === 'mla' ? 'Works Cited' : 'References'}</h2>
                                            <ul className="space-y-2">
                                                {rendered.bibliography.map((entry) => (
                                                    <li key={entry.key} className={rendered.style === 'ieee' ? '' : 'pl-6 -indent-6'}><RichText text={entry.text} /></li>
                                                ))}
                                            </ul>
                                        </>
                                    )}
                                </article>
                            )}
                            {rendered && rendered.missing.length > 0 && <p role="alert" className="text-sm text-red-700">{rendered.missing.length} cited source(s) no longer exist and show as “[source missing]”.</p>}
                            {rendered?.warnings.map((warning) => (
                                <div key={warning.book} className="text-xs text-amber-800">
                                    <p className="font-medium">⚠ {warning.book}</p>
                                    <ul className="list-disc pl-5">{warning.messages.map((text) => <li key={text}>{text}</li>)}</ul>
                                </div>
                            ))}
                        </div>
                    )}
                </aside>
            </div>
        </div>
    )
}

export function WritingView({ id }: { id: number | null }) {
    const [drafts, setDrafts] = useState<DraftSummary[] | null>(null)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        if (id !== null) return
        const controller = new AbortController()
        listDrafts(controller.signal)
            .then(setDrafts)
            .catch((failure: unknown) => {
                if (!controller.signal.aborted) setError(message(failure))
            })
        return () => controller.abort()
    }, [id])

    if (id !== null) return <Editor key={id} id={id} />

    const create = async () => {
        try {
            const draft = await createDraft('Untitled draft', '')
            window.location.hash = workspaceHref('writing', draft.id)
        } catch (failure) {
            setError(message(failure))
        }
    }

    return (
        <div className="space-y-4">
            <button type="button" onClick={() => void create()} className={PRIMARY}><FilePlus2 className="h-4 w-4" /> New draft</button>
            {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
            {drafts === null ? <p className="text-sm text-zinc-500">Loading…</p> : drafts.length === 0 ? (
                <p className="text-sm text-zinc-600">No drafts yet. Write with passages you saved: each quotation carries its citation, the bibliography builds itself, and every quotation can be checked against its source page.</p>
            ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {drafts.map((draft) => (
                        <li key={draft.id}>
                            <a href={workspaceHref('writing', draft.id)} className="block h-full border-2 border-zinc-950 p-4 hover:bg-zinc-50">
                                <p className="truncate font-semibold">{draft.title}</p>
                                <p className="line-clamp-2 text-sm text-zinc-600">{draft.excerpt || 'Empty'}</p>
                                <p className="mt-2 text-xs text-zinc-500">{draft.style.toUpperCase()}</p>
                            </a>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

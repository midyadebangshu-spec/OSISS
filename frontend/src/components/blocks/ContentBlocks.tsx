import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import type { CodeBlock, DefinitionBlock, ListBlock } from '../../types/search'
import { cleanBookText } from '../../lib/textFormat'

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The book's definition sentence, with the defined term set in bold. */
export function DefinitionView({ block, large = false }: { block: DefinitionBlock; large?: boolean }) {
    const text = cleanBookText(block.text)
    const parts = text.split(new RegExp(`(${escapeRegExp(block.term)})`, 'iu'))
    return (
        <div className="border-l-4 border-zinc-950 pl-4">
            <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">Definition</p>
            <p className={`mt-1 font-semibold tracking-tight ${large ? 'text-2xl md:text-3xl' : 'text-xl'}`}>{block.term}</p>
            <p className={`mt-2 leading-relaxed ${large ? 'text-lg' : 'text-base'}`}>
                {parts.map((part, index) =>
                    part.toLowerCase() === block.term.toLowerCase() ? (
                        <strong key={index} className="bg-amber-200 px-0.5 font-semibold">
                            {part}
                        </strong>
                    ) : (
                        <span key={index}>{part}</span>
                    ),
                )}
            </p>
        </div>
    )
}

export function ListView({ block, large = false }: { block: ListBlock; large?: boolean }) {
    const items = block.items.map((item) => cleanBookText(item).replace(/^\n?•\s*/, ''))
    const itemClass = `leading-relaxed ${large ? 'text-base md:text-lg' : 'text-sm md:text-base'}`
    return (
        <div>
            {block.title && <p className="mb-3 font-medium">{cleanBookText(block.title)}</p>}
            {block.ordered ? (
                <ol className="space-y-2">
                    {items.map((item, index) => (
                        <li key={index} className="flex gap-3">
                            <span className="flex h-7 min-w-7 shrink-0 items-center justify-center border-2 border-zinc-950 px-1 text-sm font-semibold">
                                {block.markers?.[index]?.replace(/^\(|[.)]$/g, '') || index + 1}
                            </span>
                            <span className={`${itemClass} pt-0.5`}>{item}</span>
                        </li>
                    ))}
                </ol>
            ) : (
                <ul className="space-y-2">
                    {items.map((item, index) => (
                        <li key={index} className="flex gap-3">
                            <span className="mt-2.5 h-2 w-2 shrink-0 bg-zinc-950" />
                            <span className={itemClass}>{item}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

export function CodeView({ block }: { block: CodeBlock }) {
    const [copied, setCopied] = useState(false)
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(block.text)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
        } catch {
            setCopied(false)
        }
    }
    return (
        <div className="border-2 border-zinc-950">
            <div className="flex items-center justify-between border-b-2 border-zinc-950 bg-zinc-950 px-3 py-1.5 text-xs uppercase tracking-[0.16em] text-zinc-300">
                <span>Code from the book</span>
                <button type="button" onClick={copy} className="inline-flex items-center gap-1 normal-case tracking-normal text-white hover:underline">
                    {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                    {copied ? 'Copied' : 'Copy'}
                </button>
            </div>
            <pre className="max-h-[28rem] overflow-auto bg-zinc-50 p-4 font-mono text-[13px] leading-relaxed text-zinc-900">
                <code>{block.text}</code>
            </pre>
        </div>
    )
}

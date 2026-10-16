import { Search } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { Alert, Badge, Button, Card, Input, PageHeader, Spinner } from '../components/ui.tsx'
import { formatScore } from '../lib/format.ts'
import { useAction } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { ConsoleResponse } from '../lib/types.ts'

const EXAMPLES = ['What is the boiling point of water?', 'আলোর প্রতিফলন কাকে বলে?', 'ऊष्मागतिकी की परिभाषा क्या है?', 'goti kake bole']
const STAGES = ['load', 'retrieve', 'rerank', 'diversify', 'qa']

/** Bold the QA answer inside the quote, when it occurs there. */
function Highlighted({ text, answer }: { text: string; answer: string }) {
  const index = answer ? text.indexOf(answer) : -1
  if (index < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, index)}
      <mark className="rounded bg-amber-100 px-0.5">{answer}</mark>
      {text.slice(index + answer.length)}
    </>
  )
}

export function SearchConsole() {
  const { call } = useServer()
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(3)
  const [response, setResponse] = useState<ConsoleResponse | null>(null)
  const { busy, error, run } = useAction()

  const search = (text: string) =>
    run(async () => {
      setResponse(await call<ConsoleResponse>({ method: 'POST', path: '/api/admin/search', body: { query: text, top_k: topK }, timeoutMs: 180_000 }))
    })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (query.trim()) void search(query.trim())
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Search console"
        description="Runs the same pipeline as /api/search on this server, with the scores and time spent in each stage. The first search after a restart also loads the models."
      />
      <Card>
        <form onSubmit={onSubmit} className="flex flex-wrap gap-2">
          <Input className="min-w-[16rem] flex-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask a question in Bengali, Hindi or English" aria-label="Query" />
          <select className="rounded-md border border-slate-300 bg-white px-2 text-sm" value={topK} onChange={(event) => setTopK(Number(event.target.value))} aria-label="Results">
            {[1, 3, 5, 10].map((value) => (
              <option key={value} value={value}>
                top {value}
              </option>
            ))}
          </select>
          <Button type="submit" variant="primary" icon={<Search className="h-4 w-4" />} busy={busy} disabled={!query.trim()}>
            Search
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
              onClick={() => {
                setQuery(example)
                void search(example)
              }}
            >
              {example}
            </button>
          ))}
        </div>
      </Card>
      {error && <Alert>{error}</Alert>}
      {busy && <Spinner label="Searching…" />}
      {response && !busy && (
        <>
          <Card title="Run">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="indigo">mode: {response.mode ?? '—'}</Badge>
              {Object.entries(response.variant_queries).map(([language, text]) => (
                <Badge key={language}>
                  {language}: {text}
                </Badge>
              ))}
              <span className="ml-auto font-medium">{(response.timings.total * 1000).toFixed(0)} ms total</span>
            </div>
            <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-slate-100" aria-label="Time per stage">
              {STAGES.filter((stage) => response.timings[stage]).map((stage, index) => (
                <div
                  key={stage}
                  title={`${stage}: ${(response.timings[stage] * 1000).toFixed(0)} ms`}
                  style={{ width: `${(100 * response.timings[stage]) / Math.max(response.timings.total, ...STAGES.map((name) => response.timings[name] ?? 0).filter(Boolean), 1e-9)}%` }}
                  className={['bg-slate-400', 'bg-indigo-500', 'bg-violet-500', 'bg-sky-500', 'bg-emerald-500'][index]}
                />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              {STAGES.filter((stage) => response.timings[stage] !== undefined).map((stage) => (
                <span key={stage}>
                  {stage} {(response.timings[stage] * 1000).toFixed(0)} ms
                </span>
              ))}
            </div>
          </Card>
          {!response.results.length && <Alert tone="amber">No results.</Alert>}
          {response.results.map((result, index) => (
            <Card
              key={`${result.file_path}-${result.page_number}-${index}`}
              title={
                <span>
                  #{index + 1} · {result.book_title ?? 'Unknown'} · page {result.page_number}
                </span>
              }
              actions={
                <div className="flex gap-2 text-xs text-slate-500">
                  <span>rerank {formatScore(result.rerank_score)}</span>
                  <span>QA {formatScore(result.qa_score)}</span>
                  <span>retrieval {formatScore(result.retrieval_score)}</span>
                </div>
              }
            >
              <blockquote className="border-l-4 border-indigo-200 pl-3 text-sm">
                <Highlighted text={result.quote} answer={result.answer} />
              </blockquote>
              <details className="mt-2 text-xs text-slate-500">
                <summary className="cursor-pointer">Matched paragraph</summary>
                <p className="mt-1 whitespace-pre-wrap">{result.paragraph}</p>
              </details>
              <div className="mt-2 font-mono text-[11px] text-slate-400">{result.file_path}</div>
            </Card>
          ))}
        </>
      )}
    </div>
  )
}

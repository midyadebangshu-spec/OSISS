import { Database, FolderSearch, ListRestart, Wrench, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { JobBadge } from '../components/JobStatus.tsx'
import { Alert, Button, Card, Messages, PageHeader, Spinner, Toggle } from '../components/ui.tsx'
import { formatTime, timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { IngesterState, Job, LogEntry } from '../lib/types.ts'

const MAINTENANCE = [
  { task: 'db-init', label: 'Update database schema', help: 'db_init.py: create missing tables, columns and index mappings.', icon: Database },
  { task: 'reindex', label: 'Re-index language fields', help: 'reindex_es.py: fill text.bn/hi/en for documents that lack them.', icon: ListRestart },
  { task: 'backfill-exercises', label: 'Re-apply exercise flags', help: 'backfill_exercise_flags.py: after changing the exercise heuristic.', icon: Wrench },
]

function LiveLog() {
  const { call, profile } = useServer()
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const since = useRef(0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    since.current = 0
    let stopped = false
    const poll = async () => {
      try {
        const page = await call<{ entries: LogEntry[]; last_seq: number }>({ path: `/api/admin/ingester/logs?since=${since.current}` })
        if (stopped) return
        // A restarted server numbers its log from 1 again.
        const restarted = page.last_seq < since.current
        since.current = page.last_seq
        setError(null)
        if (page.entries.length || restarted) setEntries((current) => (restarted ? page.entries : [...current, ...page.entries]).slice(-1000))
      } catch (caught) {
        if (!stopped) setError(caught instanceof Error ? caught.message : String(caught))
      }
    }
    const first = setTimeout(poll, 0)
    const timer = setInterval(poll, 2000)
    return () => {
      stopped = true
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [call, profile.id])

  useEffect(() => {
    const element = box.current
    if (element) element.scrollTop = element.scrollHeight
  }, [entries])

  return (
    <Card title="Live log">
      {error && <Alert>{error}</Alert>}
      <div ref={box} className="h-80 overflow-y-auto rounded bg-slate-900 p-3 font-mono text-xs leading-5 text-slate-100" data-testid="live-log">
        {entries.length ? (
          entries.map((entry) => (
            <div key={entry.seq} className={entry.message.includes('ERROR') || entry.message.includes('FAILED') ? 'text-rose-300' : ''}>
              <span className="text-slate-500">{formatTime(entry.time)} </span>
              <span className="whitespace-pre-wrap">{entry.message}</span>
            </div>
          ))
        ) : (
          <span className="text-slate-500">No log lines yet.</span>
        )}
      </div>
    </Card>
  )
}

export function Ingestion() {
  const { call, profile } = useServer()
  const state = useLoader(() => call<IngesterState>({ path: '/api/admin/ingester' }), 3000, [profile.id])
  const jobs = useLoader(() => call<{ jobs: Job[] }>({ path: '/api/admin/jobs' }), 2000, [profile.id])
  const { busy, error, notice, run, setError, setNotice } = useAction()

  const ingester = state.data
  return (
    <div className="space-y-5">
      <PageHeader
        title="Ingestion & jobs"
        description="The ingester runs inside the API server and reuses its loaded models. Uploads, re-ingests, restores, scans and maintenance tasks run one at a time in the order queued."
      />
      <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="PDF folder polling">
          {state.error && <Alert>{state.error}</Alert>}
          {!ingester && !state.error && <Spinner />}
          {ingester && (
            <div className="space-y-3 text-sm">
              <div className="flex items-center gap-3">
                <Toggle
                  label="Poll the PDF folder"
                  checked={Boolean(ingester.polling)}
                  disabled={busy}
                  onChange={(polling) =>
                    run(async () => {
                      await call({ method: 'POST', path: '/api/admin/ingester', body: { polling } })
                      await state.reload()
                    })
                  }
                />
                <span>
                  {ingester.polling ? `Scanning data/pdfs every ${ingester.poll_interval_seconds}s for new or changed PDFs` : 'Polling is off'}
                </span>
              </div>
              <p className="text-xs text-slate-500">
                Same work as <code>./ingestion.sh</code>; the setting is kept across server restarts. Last scan: {timeAgo(ingester.last_scan_at)}.
              </p>
              <Button
                icon={<FolderSearch className="h-4 w-4" />}
                busy={busy}
                onClick={() =>
                  run(async () => {
                    await call({ method: 'POST', path: '/api/admin/ingester/scan' })
                    await jobs.reload()
                  }, 'Scan queued.')
                }
              >
                Scan now
              </Button>
            </div>
          )}
        </Card>
        <Card title="Maintenance">
          <div className="space-y-2">
            {MAINTENANCE.map(({ task, label, help, icon: Icon }) => (
              <div key={task} className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">{label}</div>
                  <div className="text-xs text-slate-500">{help}</div>
                </div>
                <Button
                  icon={<Icon className="h-4 w-4" />}
                  busy={busy}
                  onClick={() =>
                    run(async () => {
                      await call({ method: 'POST', path: `/api/admin/maintenance/${task}` })
                      await jobs.reload()
                    }, `${label}: queued.`)
                  }
                >
                  Run
                </Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <Card title="Recent jobs">
        {jobs.error && <Alert>{jobs.error}</Alert>}
        {jobs.data && !jobs.data.jobs.length && <p className="text-sm text-slate-500">No jobs yet.</p>}
        {jobs.data && jobs.data.jobs.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-3">Job</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Queued</th>
                <th className="py-2 pr-3">Result</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {jobs.data.jobs.map((job) => (
                <tr key={job.id} className="border-b border-slate-100 align-top">
                  <td className="py-2 pr-3 font-medium">{job.label}</td>
                  <td className="py-2 pr-3">
                    <JobBadge status={job.status} />
                  </td>
                  <td className="py-2 pr-3 text-xs text-slate-500">{formatTime(job.created_at)}</td>
                  <td className={`max-w-md py-2 pr-3 text-xs ${job.status === 'failed' ? 'text-rose-700' : 'text-slate-600'}`}>
                    {job.message}
                    {job.worker && <div className="text-slate-400">on {job.worker}</div>}
                  </td>
                  <td className="py-2 text-right">
                    {job.status === 'queued' && (
                      <Button
                        variant="ghost"
                        icon={<X className="h-4 w-4" />}
                        onClick={() =>
                          run(async () => {
                            await call({ method: 'POST', path: `/api/admin/jobs/${job.id}/cancel` })
                            await jobs.reload()
                          })
                        }
                      >
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <LiveLog />
    </div>
  )
}

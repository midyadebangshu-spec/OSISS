import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'

import { errorText, useServer } from '../lib/server.ts'
import type { Job } from '../lib/types.ts'

export function JobBadge({ status }: { status: Job['status'] }) {
  const styles: Record<Job['status'], string> = {
    queued: 'text-slate-600',
    running: 'text-indigo-700',
    done: 'text-emerald-700',
    failed: 'text-rose-700',
    cancelled: 'text-slate-400',
  }
  const Icon = status === 'running' ? Loader2 : status === 'done' ? CheckCircle2 : status === 'failed' ? XCircle : Clock
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${styles[status]}`}>
      <Icon className={`h-3.5 w-3.5 ${status === 'running' ? 'animate-spin' : ''}`} />
      {status}
    </span>
  )
}

/** Follows a job until it finishes, then calls `onFinished` once. */
export function JobStatus({ jobId, onFinished }: { jobId: string; onFinished?: (job: Job) => void }) {
  const { call } = useServer()
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const current = await call<Job>({ path: `/api/admin/jobs/${jobId}` })
        if (stopped) return
        setJob(current)
        if (['done', 'failed', 'cancelled'].includes(current.status)) {
          onFinished?.(current)
          return
        }
      } catch (caught) {
        if (!stopped) setError(errorText(caught))
      }
      if (!stopped) timer = setTimeout(poll, 1500)
    }
    void poll()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
    // onFinished is intentionally not a dependency: a new callback identity must not restart polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, call])

  if (error) return <div className="text-sm text-rose-700">{error}</div>
  if (!job) return <div className="text-sm text-slate-500">Waiting for the server…</div>
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{job.label}</span>
        <JobBadge status={job.status} />
      </div>
      {job.status === 'running' && <div className="mt-1 text-xs text-slate-500">Extracting text (OCR for scanned books can take minutes), then embedding…</div>}
      {job.message && <div className={`mt-1 text-xs ${job.status === 'failed' ? 'text-rose-700' : 'text-slate-600'}`}>{job.message}</div>}
    </div>
  )
}

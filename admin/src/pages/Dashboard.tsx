import { Cpu, Database, HardDrive, Play, RefreshCw, ScrollText, Search, Square, Workflow } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import type { LocalStatus } from '../../shared/types.ts'
import { Alert, Badge, Button, Card, Messages, Modal, PageHeader, Spinner, type Tone } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { formatBytes, formatDuration, timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { Replica, SearchMetrics, ServerStatus } from '../lib/types.ts'

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(1)} s`
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold tabular-nums text-black">{value}</div>
      {hint && <div className="text-[11px] text-slate-400">{hint}</div>}
    </div>
  )
}

const PIPELINE_STAGES = ['load', 'retrieve', 'rerank', 'diversify', 'qa']

const STAGE_SHADES = ['bg-neutral-300', 'bg-neutral-900', 'bg-neutral-500', 'bg-neutral-400', 'bg-neutral-700']

function SearchLoad() {
  const { call, profile } = useServer()
  const metrics = useLoader(() => call<SearchMetrics>({ path: '/api/admin/metrics' }), 5000, [profile.id])
  const data = metrics.data
  if (!data) return metrics.error ? <Alert>{metrics.error}</Alert> : null
  // In pipeline order (stored JSON from several copies does not keep key order).
  const stages = PIPELINE_STAGES.filter((stage) => data.stage_seconds[stage]).map((stage) => [stage, data.stage_seconds[stage]] as const)
  const stageTotal = stages.reduce((sum, [, value]) => sum + (value ?? 0), 0)
  const refused = data.refused_rate_limit + data.refused_busy
  const replicas = data.replicas ?? []
  return (
    <Card
      title={replicas.length ? `Search load, all copies (last 5 minutes)` : 'Search load (last 5 minutes)'}
      actions={data.waiting > 0 ? <Badge tone="amber">{data.waiting} waiting</Badge> : <Badge tone="green">No queue</Badge>}
    >
      <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
        <Stat label="Searches / minute" value={data.searches_per_minute.toFixed(1)} hint={`${data.searches} in total`} />
        <Stat label="Typical time" value={seconds(data.latency_p50)} hint="median, without cache hits" />
        <Stat label="Slow searches" value={seconds(data.latency_p95)} hint="95th percentile" />
        <Stat label="Running now" value={`${data.running} / ${data.concurrency}`} hint={`${data.waiting} waiting (max ${data.limits.search_queue_limit})`} />
        <Stat
          label="From cache"
          value={data.cache_hit_rate === null ? '—' : `${Math.round(data.cache_hit_rate * 100)} %`}
          hint={data.limits.search_cache_size ? `${data.cache_entries} answers cached` : 'cache is off now'}
        />
        <Stat
          label="Refused"
          value={<span className={refused ? 'text-rose-700' : undefined}>{refused}</span>}
          hint={`${data.refused_rate_limit} rate limit · ${data.refused_busy} busy`}
        />
      </div>
      {stageTotal > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-xs text-slate-500">Where the time goes (average per search)</div>
          <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
            {stages.map(([stage, value], index) => (
              <div key={stage} title={`${stage}: ${seconds(value)}`} style={{ width: `${(100 * (value ?? 0)) / stageTotal}%` }} className={STAGE_SHADES[index % STAGE_SHADES.length]} />
            ))}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-slate-500">
            {stages.map(([stage, value]) => (
              <span key={stage}>
                {stage} {seconds(value)}
              </span>
            ))}
          </div>
        </div>
      )}
      {replicas.length > 0 && <ReplicaTable replicas={replicas} />}
      <div className="mt-3 text-xs text-slate-500">
        Models in {data.limits.model_precision} · rate limit{' '}
        {data.limits.search_rate_limit_per_minute ? `${data.limits.search_rate_limit_per_minute}/min per IP` : 'off'}
        {data.limits.api_key_rate_limit_per_minute ? `, ${data.limits.api_key_rate_limit_per_minute}/min per API key` : ''}. Change these
        under Search settings → Load protection.
      </div>
    </Card>
  )
}

function ReplicaTable({ replicas }: { replicas: Replica[] }) {
  const stale = replicas.filter((item) => !item.alive)
  return (
    <div className="mt-4">
      <div className="mb-1 flex items-center gap-2 text-xs text-slate-500">
        API copies
        {stale.length > 0 && <Badge tone="amber">{stale.length} not reporting</Badge>}
      </div>
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
          <tr>
            <th className="py-1.5 pr-3">Copy</th>
            <th className="py-1.5 pr-3">Role</th>
            <th className="py-1.5 pr-3">State</th>
            <th className="py-1.5 pr-3 text-right">Searches / min</th>
            <th className="py-1.5 pr-3 text-right">Typical</th>
            <th className="py-1.5 pr-3 text-right">Running / waiting</th>
            <th className="py-1.5">Last report</th>
          </tr>
        </thead>
        <tbody>
          {replicas.map((item) => (
            <tr key={item.id} className={`border-b border-slate-100 ${item.alive ? '' : 'text-slate-400'}`}>
              <td className="py-1.5 pr-3 font-medium">
                {item.id}
                {item.this && <span className="ml-1 text-xs font-normal text-slate-400">(answered this page)</span>}
              </td>
              <td className="py-1.5 pr-3">{item.ingest_worker ? 'search + ingestion' : 'search'}</td>
              <td className="py-1.5 pr-3">
                {!item.alive ? <Badge tone="amber">not reporting</Badge> : item.ready ? <Badge tone="green">ready</Badge> : <Badge tone="red">not ready</Badge>}
              </td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{item.metrics ? item.metrics.searches_per_minute.toFixed(1) : '—'}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{seconds(item.metrics?.latency_p50)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {item.metrics ? `${item.metrics.running} / ${item.metrics.waiting}` : '—'}
              </td>
              <td className="py-1.5 text-xs">{timeAgo(item.heartbeat_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {stale.length > 0 && (
        <p className="mt-1 text-xs text-slate-500">
          A copy that stops reporting has stopped or lost its database connection; the load balancer stops sending it
          searches once its /ready check fails. Rows disappear after a day.
        </p>
      )}
    </div>
  )
}

function Tile({ icon, title, tone, status, children }: { icon: ReactNode; title: string; tone: Tone; status: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
          {icon}
          {title}
        </div>
        <Badge tone={tone}>{status}</Badge>
      </div>
      <div className="space-y-0.5 text-xs text-slate-500">{children}</div>
    </div>
  )
}

function LocalControls() {
  const { profile } = useServer()
  const local = useLoader<LocalStatus>(() => osiss.local.status(profile.id), 5000, [profile.id])
  const { busy, error, notice, run, setError, setNotice } = useAction()
  const [log, setLog] = useState<string | null>(null)

  const act = (action: () => Promise<{ ok: boolean; output: string }>) =>
    run(async () => {
      const result = await action()
      await local.reload()
      if (!result.ok) throw new Error(result.output || 'Command failed.')
      return result
    }).then((result) => result && setNotice(result.output.split('\n').slice(-3).join('\n') || 'Done.'))

  const state = local.data
  if (!state) return local.error ? <Alert>{local.error}</Alert> : <Spinner label="Checking local services…" />
  if (!state.supported) {
    return (
      <Card title="This computer">
        <Alert tone="amber">{state.reason}</Alert>
      </Card>
    )
  }
  const services = new Map(state.docker.services.map((item) => [item.name, item]))
  return (
    <Card title="Services on this computer" actions={<Button variant="ghost" icon={<RefreshCw className="h-4 w-4" />} onClick={() => local.reload()} aria-label="Refresh" />}>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="mb-2 text-sm font-medium">Docker: PostgreSQL + Elasticsearch</div>
          {!state.docker.available ? (
            <Alert tone="amber">{state.docker.error ?? 'Docker is not available.'}</Alert>
          ) : (
            <ul className="mb-3 space-y-1 text-sm">
              {['postgres', 'elasticsearch'].map((name) => {
                const service = services.get(name)
                const running = service?.state === 'running'
                return (
                  <li key={name} className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${running ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                    <span className="w-28">{name}</span>
                    <span className="text-xs text-slate-500">{service ? `${service.state}${service.health ? ` (${service.health})` : ''}` : 'not created'}</span>
                  </li>
                )
              })}
            </ul>
          )}
          <div className="flex gap-2">
            <Button icon={<Play className="h-4 w-4" />} busy={busy} disabled={!state.docker.available} onClick={() => act(() => osiss.local.docker(profile.id, 'up'))}>
              Start
            </Button>
            <Button icon={<Square className="h-4 w-4" />} busy={busy} disabled={!state.docker.available} onClick={() => act(() => osiss.local.docker(profile.id, 'stop'))}>
              Stop
            </Button>
          </div>
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">OSISS API (api.sh)</div>
          <div className="mb-3 flex items-center gap-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${state.api.healthy ? 'bg-emerald-500' : state.api.running ? 'bg-amber-500' : 'bg-slate-300'}`} />
            {state.api.healthy ? 'Running' : state.api.running ? 'Starting…' : 'Stopped'}
            {state.api.pid && <span className="text-xs text-slate-500">PID {state.api.pid}</span>}
            {state.api.healthy && !state.api.startedByApp && <span className="text-xs text-slate-500">(started outside this app)</span>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button icon={<Play className="h-4 w-4" />} busy={busy} disabled={state.api.running} onClick={() => act(() => osiss.local.api(profile.id, 'start'))}>
              Start API
            </Button>
            <Button icon={<Square className="h-4 w-4" />} busy={busy} disabled={!state.api.startedByApp} onClick={() => act(() => osiss.local.api(profile.id, 'stop'))}>
              Stop API
            </Button>
            <Button variant="ghost" icon={<ScrollText className="h-4 w-4" />} onClick={async () => setLog(await osiss.local.apiLog(profile.id))}>
              API output
            </Button>
          </div>
        </div>
      </div>
      <div className="mt-3">
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      </div>
      {log !== null && (
        <Modal title="API output (started from this app)" wide onClose={() => setLog(null)}>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{log || 'No output yet.'}</pre>
        </Modal>
      )}
    </Card>
  )
}

export function Dashboard() {
  const { call, profile } = useServer()
  const status = useLoader(() => call<ServerStatus>({ path: '/api/admin/status', timeoutMs: 15_000 }), 5000, [profile.id])
  const data = status.data

  return (
    <div className="space-y-5">
      <PageHeader
        title="Dashboard"
        description={`${profile.name} · ${profile.url}`}
        actions={<Button icon={<RefreshCw className="h-4 w-4" />} onClick={() => status.reload()}>Refresh</Button>}
      />
      {profile.kind === 'local' && <LocalControls />}
      {status.error && !data && <Alert>{status.error}</Alert>}
      {!data && !status.error && <Spinner label="Loading server status…" />}
      {data && (
        <>
          {data.warnings.map((warning) => (
            <Alert key={warning} tone="amber">
              {warning}
            </Alert>
          ))}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <Tile icon={<Database className="h-4 w-4" />} title="PostgreSQL" tone={data.postgres.ok ? 'green' : 'red'} status={data.postgres.ok ? 'Up' : 'Down'}>
              {data.postgres.ok ? (
                <>
                  <div>{data.postgres.books} books · {data.postgres.chunks} chunks · {data.postgres.pages} pages</div>
                  <div>{data.postgres.excluded} deleted (files kept) · {data.postgres.api_keys} active API keys</div>
                </>
              ) : (
                <div className="break-words">{data.postgres.error}</div>
              )}
            </Tile>
            <Tile
              icon={<Search className="h-4 w-4" />}
              title="Elasticsearch"
              tone={!data.elasticsearch.ok ? 'red' : data.elasticsearch.cluster_status === 'red' ? 'red' : data.elasticsearch.index_exists ? 'green' : 'amber'}
              status={!data.elasticsearch.ok ? 'Down' : data.elasticsearch.index_exists ? (data.elasticsearch.cluster_status ?? 'Up') : 'No index'}
            >
              {data.elasticsearch.ok ? (
                <div>
                  Index <code>{data.elasticsearch.index}</code>: {data.elasticsearch.documents} documents
                </div>
              ) : (
                <div className="break-words">{data.elasticsearch.error}</div>
              )}
            </Tile>
            <Tile
              icon={<Workflow className="h-4 w-4" />}
              title="Ingester"
              tone={data.ingester.current ? 'dark' : data.ingester.running || data.ingester.worker_here === false ? 'green' : 'slate'}
              status={
                data.ingester.current
                  ? 'Working'
                  : data.ingester.worker_here === false
                    ? 'On the worker'
                    : !data.ingester.running
                      ? 'Off'
                      : data.ingester.polling
                        ? 'Polling'
                        : 'Idle'
              }
            >
              {data.ingester.running || data.ingester.worker_here === false ? (
                <>
                  <div>{data.ingester.current ? data.ingester.current.label : `${data.ingester.queued ?? 0} job(s) queued`}</div>
                  <div>
                    Polling {data.ingester.polling ? `every ${data.ingester.poll_interval_seconds}s` : 'off'} · last scan {timeAgo(data.ingester.last_scan_at)}
                  </div>
                  {data.ingester.worker_here === false && <div>This copy only queues jobs; a worker copy runs them.</div>}
                </>
              ) : (
                <div>The ingestion worker is not running (ADMIN_TOKEN unset at startup?).</div>
              )}
            </Tile>
            <Tile
              icon={<Cpu className="h-4 w-4" />}
              title="Models"
              tone={data.models.every((model) => model.present || !model.enabled) ? 'green' : 'amber'}
              status={`${data.models.filter((model) => model.loaded).length}/${data.models.length} loaded`}
            >
              {data.models.map((model) => (
                <div key={model.key} className="flex items-center justify-between gap-2">
                  <span>{model.name}</span>
                  <span>{!model.enabled ? 'disabled' : !model.present ? 'missing' : model.loaded ? 'loaded' : 'on first search'}</span>
                </div>
              ))}
            </Tile>
            <Tile icon={<Cpu className="h-4 w-4" />} title="GPU" tone={data.gpu.available ? 'green' : 'slate'} status={data.gpu.available ? 'CUDA' : 'CPU'}>
              {data.gpu.available ? (
                <div>
                  {data.gpu.name} · {formatBytes(data.gpu.memory_free)} free of {formatBytes(data.gpu.memory_total)}
                </div>
              ) : (
                <div>No CUDA device; inference runs on the CPU (INFERENCE_DEVICE={data.gpu.device}).</div>
              )}
            </Tile>
            <Tile
              icon={<HardDrive className="h-4 w-4" />}
              title="Disk"
              tone={data.disk.free / data.disk.total < 0.1 ? 'red' : 'green'}
              status={`${formatBytes(data.disk.free)} free`}
            >
              <div className="truncate">{data.disk.path}</div>
              <div>
                {formatBytes(data.disk.used)} used of {formatBytes(data.disk.total)}
              </div>
            </Tile>
          </div>
          <SearchLoad />
          <Card title="Server">
            <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-slate-600">
              <span>OSISS {data.version}</span>
              <span>Up {formatDuration(data.uptime_seconds)}</span>
              <span>
                Search access: <b>{data.access.mode}</b>
                {!data.access.configured && ' (not chosen yet)'}
              </span>
              <span>Admin IP allowlist: {data.admin_ip_restricted ? 'on' : 'off'}</span>
              {data.deployment && (
                <>
                  <span>
                    Shared state: <b>{data.deployment.state_backend === 'postgres' ? 'PostgreSQL (several copies)' : 'this process only'}</b>
                  </span>
                  <span title={data.deployment.storage_root}>
                    PDFs: <b>{data.deployment.storage_backend === 's3' ? 'S3' : 'local folder'}</b> ({data.deployment.storage_root})
                  </span>
                  <span>Redis: {data.deployment.redis ? 'on' : 'off'}</span>
                  <span>
                    This copy: <b>{data.deployment.replica_id}</b> ({data.deployment.ingest_worker ? 'search + ingestion' : 'search only'})
                  </span>
                </>
              )}
              {data.access.public_base_url && <span>Public URL: {data.access.public_base_url}</span>}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

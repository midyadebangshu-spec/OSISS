import { Copy, ExternalLink, Hammer, Power, PowerOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Alert, Badge, Button, Card, Field, Input, Messages, PageHeader, Spinner } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { buildCaddyfile, domainError, ipError, publicSteps } from '../lib/caddy.ts'
import { formatBytes, timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { WebsiteStatus } from '../lib/types.ts'

function Log({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines.length])
  return (
    <pre ref={box} className="mt-3 max-h-64 overflow-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100" aria-label="Build log">
      {lines.join('\n')}
    </pre>
  )
}

function PublicAddress({ port }: { port: string }) {
  const [domain, setDomain] = useState('')
  const [ip, setIp] = useState('')
  const problem = domain.trim() ? domainError(domain) : null
  const ipProblem = ipError(ip)
  const ready = domain.trim() !== '' && !problem && !ipProblem
  const file = ready ? buildCaddyfile(domain, ip, Number(port) || 8000) : ''
  const steps = ready ? publicSteps(domain).join('\n') : ''
  return (
    <Card title="Put it on your own domain">
      <p className="mb-3 text-xs text-slate-500">
        This app cannot buy a domain, change DNS or install HTTPS for you. Fill in the two boxes and it prepares the Caddy config and the
        commands to run on the server (the README’s section 11.5).
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Your domain" hint="For example search.example.edu (no https://).">
          <Input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="search.example.edu" />
        </Field>
        <Field label="This admin PC’s public IP (optional)" hint="Only this address may reach /api/admin/* through the domain. Leave empty to allow all.">
          <Input value={ip} onChange={(event) => setIp(event.target.value)} placeholder="203.0.113.10" />
        </Field>
      </div>
      {(problem || ipProblem) && <div className="mt-3"><Alert tone="amber">{problem ?? ipProblem}</Alert></div>}
      {ready && (
        <div className="mt-4 space-y-4">
          <div>
            <div className="mb-1 flex items-center justify-between text-xs font-medium text-slate-600">
              /etc/caddy/Caddyfile
              <Button variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => osiss.copyText(file)}>Copy</Button>
            </div>
            <pre className="overflow-x-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{file}</pre>
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between text-xs font-medium text-slate-600">
              Steps on the server
              <Button variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => osiss.copyText(steps)}>Copy</Button>
            </div>
            <pre className="overflow-x-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{steps}</pre>
          </div>
        </div>
      )}
    </Card>
  )
}

export function WebsitePage() {
  const { call, profile } = useServer()
  const status = useLoader(() => call<WebsiteStatus>({ path: '/api/admin/website' }), 2500, [profile.id])
  const { busy, error, notice, run, setError, setNotice } = useAction()
  const data = status.data

  const base = (data?.public_base_url || profile.url).replace(/\/$/, '')
  const port = (() => {
    try {
      return new URL(profile.url).port || '8000'
    } catch {
      return '8000'
    }
  })()
  const building = data?.build.state === 'running'

  const post = (method: 'POST' | 'PUT', path: string, body?: unknown, done?: string) =>
    run(async () => {
      await call<WebsiteStatus>({ method, path, body })
      await status.reload()
    }, done)

  return (
    <div className="space-y-5">
      <PageHeader title="Website" description="Build the website and put it live from the OSISS server itself. Nothing is live until you click Go live." />
      <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      {!data ? (
        status.error ? <Alert>{status.error}</Alert> : <Spinner />
      ) : (
        <>
          <Card title="This server">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={data.built ? 'green' : 'slate'}>{data.built ? `Built ${timeAgo(data.built_at)} · ${formatBytes(data.size_bytes)}` : 'Not built yet'}</Badge>
              <Badge tone={data.live ? 'green' : 'amber'}>{data.live ? 'Live' : 'Offline'}</Badge>
              {building && <Badge tone="dark">Building…</Badge>}
              {data.build.state === 'failed' && <Badge tone="red">Last build failed</Badge>}
            </div>

            {data.live && (
              <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md bg-slate-50 p-3 text-sm">
                <span className="text-slate-500">Visitors open</span>
                <code className="rounded bg-white px-2 py-0.5 font-mono text-xs ring-1 ring-slate-200">{base}</code>
                <Button variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => osiss.copyText(base)}>Copy</Button>
                <Button
                  variant="ghost"
                  icon={<ExternalLink className="h-3.5 w-3.5" />}
                  onClick={() => run(async () => {
                    const result = await osiss.openExternal(base)
                    if (!result.ok) throw new Error(result.error ?? 'Could not open the address.')
                  })}
                >
                  Open
                </Button>
              </div>
            )}
            {data.live && data.search_access === 'open' && data.public_base_url.startsWith('https://') && (
              <div className="mt-3"><Alert tone="amber">Search is open to everyone on this public address. Change it under Access &amp; API keys if that is not what you want.</Alert></div>
            )}
            {!data.node.ok && (
              <div className="mt-3"><Alert tone="amber">{data.node.message}</Alert></div>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                icon={<Hammer className="h-4 w-4" />}
                busy={busy || building}
                disabled={!data.node.ok}
                onClick={() => post('POST', '/api/admin/website/build', undefined, 'Build started. The first one installs packages and can take a few minutes.')}
              >
                {data.built ? 'Rebuild website' : 'Build website'}
              </Button>
              {data.live ? (
                <Button variant="danger" icon={<PowerOff className="h-4 w-4" />} busy={busy} onClick={() => post('PUT', '/api/admin/website', { live: false }, 'The website is offline. Search through the API still works.')}>
                  Take offline
                </Button>
              ) : (
                <Button variant="primary" icon={<Power className="h-4 w-4" />} busy={busy} disabled={!data.built || building} onClick={() => post('PUT', '/api/admin/website', { live: true }, 'The website is live.')}>
                  Go live
                </Button>
              )}
            </div>
            <p className="mt-3 text-xs text-slate-500">
              A new build shows up at once when the website is live, with no restart. Going live and offline only affects the pages; search
              through the API and the admin app keep working. Needs Node.js 20.19+ or 22.12+ on the server{data.node.node ? ` (found ${data.node.node})` : ''}.
            </p>
            {data.build.log.length > 0 && <Log lines={data.build.log} />}
            {data.build.state === 'failed' && <div className="mt-3"><Alert>{data.build.message}</Alert></div>}
          </Card>
          <PublicAddress port={port} />
        </>
      )}
    </div>
  )
}

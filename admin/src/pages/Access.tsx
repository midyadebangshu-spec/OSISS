import { Copy, KeyRound, Plus, Save, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { AccessModeChoice, type AccessMode } from '../components/AccessModeChoice.tsx'
import { Alert, Button, Card, Field, Input, Messages, Modal, PageHeader, Spinner, Toggle } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { Access, ApiKey } from '../lib/types.ts'

function AccessForm({ access, onSaved }: { access: Access; onSaved: () => void }) {
  const { call } = useServer()
  const [mode, setMode] = useState<AccessMode>(access.search_access)
  const [origins, setOrigins] = useState(access.cors_origins.join('\n'))
  const [publicUrl, setPublicUrl] = useState(access.public_base_url)
  const { busy, error, notice, run, setError, setNotice } = useAction()

  const save = () =>
    run(async () => {
      await call({
        method: 'PUT',
        path: '/api/admin/access',
        body: { search_access: mode, cors_origins: origins.split(/[\s,]+/).filter(Boolean), public_base_url: publicUrl.trim() },
      })
      onSaved()
    }, 'Access settings saved and applied.')

  return (
    <Card
      title="Who may search"
      actions={
        <Button variant="primary" icon={<Save className="h-4 w-4" />} busy={busy} onClick={save}>
          Save
        </Button>
      }
    >
      {!access.configured && <div className="mb-3"><Alert tone="amber">No choice saved yet: the server is behaving as “open”.</Alert></div>}
      <AccessModeChoice value={mode} onChange={setMode} />
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Field label="Website domains (allowed origins)" hint="One per line. Browser pages on these origins may call the API (CORS) and, in key mode, need no key.">
          <textarea
            className="h-24 w-full rounded-md border border-slate-300 px-3 py-1.5 font-mono text-xs"
            value={origins}
            onChange={(event) => setOrigins(event.target.value)}
            placeholder={'https://search.college.edu\nhttp://localhost:5173'}
          />
        </Field>
        <Field label="Public URL of this server" hint="Used to make pdf_link absolute in API results, so apps can open the PDFs. Leave empty for relative links.">
          <Input value={publicUrl} onChange={(event) => setPublicUrl(event.target.value)} placeholder="https://search.college.edu" />
        </Field>
      </div>
      <div className="mt-3">
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      </div>
    </Card>
  )
}

function ApiKeys({ access }: { access: Access }) {
  const { call, profile } = useServer()
  const keys = useLoader(() => call<{ keys: ApiKey[] }>({ path: '/api/admin/keys' }), undefined, [profile.id])
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [created, setCreated] = useState<ApiKey | null>(null)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)
  const { busy, error, run, setError } = useAction()

  const base = access.public_base_url || profile.url
  const example = `curl -X POST ${base}/api/search \\\n  -H 'Content-Type: application/json' \\\n  -H 'X-API-Key: ${created?.key ?? 'osk_…'}' \\\n  -d '{"query": "What is thermodynamics?", "top_k": 3}'`

  return (
    <Card
      title="API keys for apps"
      actions={
        <Button icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>
          New key
        </Button>
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        {access.search_access === 'key'
          ? 'Required for apps: requests without a valid key get 401.'
          : 'Optional in open mode: apps that send a key show up here with their usage.'}{' '}
        Only a hash of each key is stored on the server.
      </p>
      {error && <Alert onClose={() => setError(null)}>{error}</Alert>}
      {!keys.data ? (
        keys.error ? <Alert>{keys.error}</Alert> : <Spinner />
      ) : keys.data.keys.length === 0 ? (
        <p className="text-sm text-slate-500">No keys yet.</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
            <tr>
              <th className="py-2 pr-3">App</th>
              <th className="py-2 pr-3">Key</th>
              <th className="py-2 pr-3 text-right">Requests</th>
              <th className="py-2 pr-3">Last used</th>
              <th className="py-2 pr-3">Enabled</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {keys.data.keys.map((key) => (
              <tr key={key.id} className="border-b border-slate-100">
                <td className="py-2 pr-3 font-medium">{key.name}</td>
                <td className="py-2 pr-3 font-mono text-xs">{key.key_prefix}…</td>
                <td className="py-2 pr-3 text-right tabular-nums">{key.request_count}</td>
                <td className="py-2 pr-3 text-xs text-slate-500">{timeAgo(key.last_used_at)}</td>
                <td className="py-2 pr-3">
                  <Toggle
                    label={`Enable ${key.name}`}
                    checked={key.enabled}
                    disabled={busy}
                    onChange={(enabled) =>
                      run(async () => {
                        await call({ method: 'PATCH', path: `/api/admin/keys/${key.id}`, body: { enabled } })
                        await keys.reload()
                      })
                    }
                  />
                </td>
                <td className="py-2 text-right">
                  <Button variant="ghost" aria-label={`Delete ${key.name}`} icon={<Trash2 className="h-4 w-4" />} onClick={() => setRevoking(key)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs font-medium text-slate-600">
          For app developers
          <Button variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => osiss.copyText(example)}>
            Copy
          </Button>
        </div>
        <pre className="overflow-x-auto rounded bg-slate-900 p-3 font-mono text-xs text-slate-100">{example}</pre>
        <p className="mt-1 text-xs text-slate-500">
          Response: <code>{'{query, results: [{exact_quote, answer_text, book_title, author, department, page_number, pdf_link, paragraph_text}]}'}</code>.
          PDF links accept the key as <code>?key=…</code> in key mode.
        </p>
      </div>

      {creating && (
        <Modal
          title="New API key"
          onClose={() => setCreating(false)}
          footer={
            <>
              <Button onClick={() => setCreating(false)}>Cancel</Button>
              <Button
                variant="primary"
                icon={<KeyRound className="h-4 w-4" />}
                busy={busy}
                disabled={!name.trim()}
                onClick={() =>
                  run(async () => {
                    const key = await call<ApiKey>({ method: 'POST', path: '/api/admin/keys', body: { name: name.trim() } })
                    setCreated(key)
                    setCreating(false)
                    setName('')
                    await keys.reload()
                  })
                }
              >
                Create
              </Button>
            </>
          }
        >
          <Field label="Which app is this for?" hint="Shown in the list so you know what to revoke.">
            <Input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Library mobile app" />
          </Field>
        </Modal>
      )}
      {created?.key && (
        <Modal title={`Key for “${created.name}”`} onClose={() => setCreated(null)} footer={<Button variant="primary" onClick={() => setCreated(null)}>I have saved it</Button>}>
          <Alert tone="amber">Copy it now. The server keeps only a hash, so it cannot be shown again.</Alert>
          <div className="mt-3 flex items-center gap-2">
            <code className="flex-1 break-all rounded bg-slate-100 p-2 font-mono text-xs" data-testid="new-api-key">{created.key}</code>
            <Button icon={<Copy className="h-4 w-4" />} onClick={() => osiss.copyText(created.key!)}>
              Copy
            </Button>
          </div>
        </Modal>
      )}
      {revoking && (
        <Modal
          title={`Delete the key for “${revoking.name}”?`}
          onClose={() => setRevoking(null)}
          footer={
            <>
              <Button onClick={() => setRevoking(null)}>Cancel</Button>
              <Button
                variant="danger"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    await call({ method: 'DELETE', path: `/api/admin/keys/${revoking.id}` })
                    setRevoking(null)
                    await keys.reload()
                  })
                }
              >
                Delete key
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">The app stops working at once (in key mode). To pause it instead, switch the key off.</p>
        </Modal>
      )}
    </Card>
  )
}

export function AccessPage({ onChange }: { onChange: () => void }) {
  const { call, profile } = useServer()
  const access = useLoader(() => call<Access>({ path: '/api/admin/access' }), undefined, [profile.id])

  return (
    <div className="space-y-5">
      <PageHeader
        title="Access & API keys"
        description="Serve search to people through a website on your domain, and to other apps through the API."
      />
      {!access.data ? (
        access.error ? <Alert>{access.error}</Alert> : <Spinner />
      ) : (
        <>
          {access.data.search_access === 'open' && access.data.public_base_url && (
            <Alert tone="amber">This server has a public URL and open access: anyone on the internet can search it and download the PDFs.</Alert>
          )}
          <AccessForm
            key={JSON.stringify(access.data)}
            access={access.data}
            onSaved={() => {
              void access.reload()
              onChange()
            }}
          />
          <ApiKeys access={access.data} />
          <Card title="Serving the website on a domain">
            <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-600">
              <li>
                Build the website: <code>cd frontend &amp;&amp; npm run build</code>, and set <code>SERVE_FRONTEND=true</code> so the API serves it at <code>/</code>.
              </li>
              <li>Put an HTTPS reverse proxy (Caddy or nginx) in front of port 8000 for your domain. The README has an example.</li>
              <li>Enter the domain above as the public URL, and add it to the allowed origins if the website is hosted elsewhere.</li>
            </ol>
          </Card>
        </>
      )}
    </div>
  )
}

import { FolderOpen, Plug, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Profile, ProfileInput } from '../../shared/types.ts'
import { Alert, Badge, Button, Card, Field, Input, Messages, Modal, PageHeader } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { useAction } from '../lib/hooks.ts'
import { callApi } from '../lib/server.ts'

type Draft = ProfileInput & { token: string }

const EMPTY_LOCAL: Draft = { name: 'This computer', url: 'http://localhost:8000', kind: 'local', repoPath: '', token: '' }

function ProfileForm({
  initial,
  onSaved,
  onPersisted,
  onCancel,
}: {
  initial: Draft
  onSaved: (id: string) => void
  onPersisted: (id: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<Draft>(initial)
  const { busy, error, notice, run, setError, setNotice } = useAction()
  // Once saved (also by "test" or "read token"), an empty token field means "keep the stored token".
  const editing = Boolean(draft.id)
  const update = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }))

  const save = async (): Promise<string | undefined> => {
    const { token, ...rest } = draft
    const result = await osiss.profiles.save({ ...rest, ...(token || !editing ? { token } : {}) })
    if (!result.ok) {
      setError(result.error)
      return undefined
    }
    update({ id: result.profile.id, token: '' })
    onPersisted(result.profile.id)
    return result.profile.id
  }

  const test = () =>
    run(async () => {
      const id = await save()
      if (!id) throw new Error('Fix the fields above first.')
      const status = await callApi<{ version: string }>(id, { path: '/api/admin/status', timeoutMs: 15_000 })
      return status
    }).then((status) => status && setNotice(`Connected: OSISS ${status.version}, admin token accepted.`))

  return (
    <Card title={initial.id ? `Edit “${initial.name}”` : 'Add a server'}>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name">
          <Input value={draft.name} onChange={(event) => update({ name: event.target.value })} />
        </Field>
        <Field label="Where does the server run?">
          <select
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
            value={draft.kind}
            onChange={(event) => {
              const kind = event.target.value as Draft['kind']
              update(kind === 'local' ? { kind, url: 'http://localhost:8000' } : { kind, url: 'https://' })
            }}
          >
            <option value="local">On this computer (can start / stop it)</option>
            <option value="remote">Elsewhere (a domain or another machine)</option>
          </select>
        </Field>
        <Field
          label="Server URL"
          hint={draft.kind === 'local' ? 'Usually http://localhost:8000 (api.sh).' : 'Use https:// so the admin token is encrypted in transit.'}
        >
          <Input value={draft.url} onChange={(event) => update({ url: event.target.value })} placeholder="https://search.college.edu" />
        </Field>
        <Field label="Admin token" hint={editing ? 'Leave empty to keep the saved token.' : 'ADMIN_TOKEN from the server’s .env file.'}>
          <Input type="password" value={draft.token} onChange={(event) => update({ token: event.target.value })} placeholder={editing ? '•••••• (unchanged)' : ''} autoComplete="off" />
        </Field>
        {draft.kind === 'local' && (
          <Field label="OSISS folder on this computer" hint="Needed to start Docker and the API, and to read the token from .env.">
            <div className="flex gap-2">
              <Input value={draft.repoPath ?? ''} onChange={(event) => update({ repoPath: event.target.value })} placeholder="/home/admin/OSISS" />
              <Button
                icon={<FolderOpen className="h-4 w-4" />}
                onClick={async () => {
                  const dir = await osiss.files.pickDirectory()
                  if (dir) update({ repoPath: dir })
                }}
              >
                Browse
              </Button>
            </div>
          </Field>
        )}
        {draft.kind === 'remote' && (
          <label className="flex items-start gap-2 text-sm text-slate-600 md:col-span-2">
            <input type="checkbox" className="mt-1" checked={Boolean(draft.allowInsecureHttp)} onChange={(event) => update({ allowInsecureHttp: event.target.checked })} />
            <span>
              Allow plain <code>http://</code> to this server. Only on a trusted local network: the admin token is sent unencrypted.
            </span>
          </label>
        )}
      </div>
      <div className="mt-4 space-y-3">
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            busy={busy}
            onClick={async () => {
              const id = await save()
              if (id) onSaved(id)
            }}
          >
            Save
          </Button>
          <Button icon={<Plug className="h-4 w-4" />} busy={busy} onClick={test}>
            Save &amp; test connection
          </Button>
          {draft.kind === 'local' && (
            <Button
              busy={busy}
              onClick={() =>
                run(async () => {
                  const id = await save()
                  if (!id) throw new Error('Fix the fields above first.')
                  const result = await osiss.profiles.tokenFromEnv(id)
                  if (!result.ok) throw new Error(result.error)
                }, 'Token read from .env and saved.')
              }
            >
              Read token from .env
            </Button>
          )}
          <Button variant="ghost" onClick={onCancel}>
            Close
          </Button>
        </div>
      </div>
    </Card>
  )
}

export function Servers({
  profiles,
  activeId,
  onChanged,
  onSelect,
}: {
  profiles: Profile[]
  activeId: string | null
  onChanged: (selectId?: string) => void
  onSelect: (id: string) => void
}) {
  const [editing, setEditing] = useState<Draft | null>(profiles.length ? null : EMPTY_LOCAL)
  const [removing, setRemoving] = useState<Profile | null>(null)

  return (
    <div className="space-y-5">
      <PageHeader
        title="Servers"
        description="OSISS servers this app controls. A server on this computer can also be started and stopped from the Dashboard. Tokens are stored encrypted with your passkey."
        actions={
          <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing(profiles.length ? { ...EMPTY_LOCAL, name: '', kind: 'remote', url: 'https://' } : EMPTY_LOCAL)}>
            Add server
          </Button>
        }
      />
      {!profiles.length && !editing && <Alert tone="amber">Add a server to get started.</Alert>}
      {editing && (
        <ProfileForm
          key={editing.id ?? 'new'}
          initial={editing}
          onSaved={(id) => {
            setEditing(null)
            onChanged(id)
          }}
          onPersisted={(id) => onChanged(id)}
          onCancel={() => {
            setEditing(null)
            onChanged()
          }}
        />
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {profiles.map((profile) => (
          <Card
            key={profile.id}
            title={
              <span className="flex items-center gap-2">
                {profile.name}
                {profile.id === activeId && <Badge tone="indigo">Active</Badge>}
              </span>
            }
            actions={
              <>
                <Button variant="ghost" onClick={() => setEditing({ ...profile, token: '' })}>
                  Edit
                </Button>
                <Button variant="ghost" aria-label={`Remove ${profile.name}`} icon={<Trash2 className="h-4 w-4" />} onClick={() => setRemoving(profile)} />
              </>
            }
          >
            <dl className="space-y-1 text-sm">
              <div className="flex gap-2">
                <dt className="w-20 text-slate-500">URL</dt>
                <dd className="truncate font-mono text-xs leading-5">{profile.url}</dd>
              </div>
              <div className="flex gap-2">
                <dt className="w-20 text-slate-500">Type</dt>
                <dd>{profile.kind === 'local' ? 'This computer' : 'Remote'}</dd>
              </div>
              {profile.repoPath && (
                <div className="flex gap-2">
                  <dt className="w-20 text-slate-500">Folder</dt>
                  <dd className="truncate font-mono text-xs leading-5">{profile.repoPath}</dd>
                </div>
              )}
              <div className="flex gap-2">
                <dt className="w-20 text-slate-500">Token</dt>
                <dd>{profile.hasToken ? <Badge tone="green">saved</Badge> : <Badge tone="amber">missing</Badge>}</dd>
              </div>
            </dl>
            {profile.id !== activeId && (
              <Button className="mt-3" onClick={() => onSelect(profile.id)}>
                Switch to this server
              </Button>
            )}
          </Card>
        ))}
      </div>
      {removing && (
        <Modal
          title={`Remove “${removing.name}”?`}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button onClick={() => setRemoving(null)}>Cancel</Button>
              <Button
                variant="danger"
                onClick={async () => {
                  await osiss.profiles.remove(removing.id)
                  setRemoving(null)
                  onChanged()
                }}
              >
                Remove
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">This only removes it from this app. The server itself is not changed.</p>
        </Modal>
      )}
    </div>
  )
}

import { Copy, KeyRound, Plus, Trash2, UserPlus } from 'lucide-react'
import { useState } from 'react'

import { Alert, Badge, Button, Card, Field, Input, Messages, Modal, PageHeader, Spinner, Toggle } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { Scholar } from '../lib/types.ts'

type Listing = { users: Scholar[]; registration: string }

export function ScholarsPage() {
  const { call, profile } = useServer()
  const list = useLoader(() => call<Listing>({ path: '/api/admin/users' }), undefined, [profile.id])
  const [creating, setCreating] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [resetting, setResetting] = useState<Scholar | null>(null)
  const [temporary, setTemporary] = useState<{ scholar: Scholar; password: string } | null>(null)
  const [deleting, setDeleting] = useState<Scholar | null>(null)
  const { busy, error, notice, run, setError, setNotice } = useAction()

  const closeCreate = () => {
    setCreating(false)
    setEmail('')
    setName('')
    setPassword('')
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Scholars"
        description="Accounts for the website's workspace (saved passages, collections, notes). There is no email: when someone forgets their password, reset it here and give them the temporary one."
      />
      <Card
        title="Accounts"
        actions={
          <Button icon={<UserPlus className="h-4 w-4" />} onClick={() => setCreating(true)}>
            New account
          </Button>
        }
      >
        <p className="mb-3 text-xs text-slate-500">
          {list.data?.registration === 'open'
            ? 'Anyone can create an account on the website (REGISTRATION_MODE=open).'
            : 'Registration is closed: only you can create accounts (REGISTRATION_MODE=closed).'}
        </p>
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
        {!list.data ? (
          list.error ? <Alert>{list.error}</Alert> : <Spinner />
        ) : list.data.users.length === 0 ? (
          <p className="text-sm text-slate-500">No scholars have signed up yet.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-3">Scholar</th>
                <th className="py-2 pr-3 text-right">Saved</th>
                <th className="py-2 pr-3 text-right">Notes</th>
                <th className="py-2 pr-3">Last sign-in</th>
                <th className="py-2 pr-3">Active</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {list.data.users.map((scholar) => (
                <tr key={scholar.id} className="border-b border-slate-100">
                  <td className="py-2 pr-3">
                    <div className="font-medium">{scholar.name || scholar.email}</div>
                    {scholar.name && <div className="text-xs text-slate-500">{scholar.email}</div>}
                    {scholar.must_change_password && <Badge tone="amber">must choose a new password</Badge>}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{scholar.evidence_count}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{scholar.note_count}</td>
                  <td className="py-2 pr-3 text-xs text-slate-500">{scholar.last_login_at ? timeAgo(scholar.last_login_at) : 'never'}</td>
                  <td className="py-2 pr-3">
                    <Toggle
                      label={`Account active for ${scholar.email}`}
                      checked={!scholar.disabled}
                      disabled={busy}
                      onChange={(active) =>
                        run(async () => {
                          await call({ method: 'PATCH', path: `/api/admin/users/${scholar.id}`, body: { disabled: !active } })
                          await list.reload()
                        })
                      }
                    />
                  </td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <Button variant="ghost" aria-label={`Reset password for ${scholar.email}`} icon={<KeyRound className="h-4 w-4" />} onClick={() => setResetting(scholar)}>
                      Reset password
                    </Button>
                    <Button variant="ghost" aria-label={`Delete ${scholar.email}`} icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(scholar)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {creating && (
        <Modal
          title="New account"
          onClose={closeCreate}
          footer={
            <>
              <Button onClick={closeCreate}>Cancel</Button>
              <Button
                variant="primary"
                icon={<Plus className="h-4 w-4" />}
                busy={busy}
                disabled={!email.trim() || password.length < 8}
                onClick={() =>
                  run(async () => {
                    await call({ method: 'POST', path: '/api/admin/users', body: { email: email.trim(), name: name.trim(), password } })
                    closeCreate()
                    await list.reload()
                  }, 'Account created. They must choose a new password at their first sign-in.')
                }
              >
                Create
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <Field label="Email">
              <Input autoFocus type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </Field>
            <Field label="Name (optional)">
              <Input value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Field label="Temporary password" hint="At least 8 characters. Give it to the scholar; they must replace it when they first sign in.">
              <Input type="text" value={password} onChange={(event) => setPassword(event.target.value)} />
            </Field>
          </div>
        </Modal>
      )}

      {resetting && (
        <Modal
          title={`Reset the password for ${resetting.email}?`}
          onClose={() => setResetting(null)}
          footer={
            <>
              <Button onClick={() => setResetting(null)}>Cancel</Button>
              <Button
                variant="primary"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    const result = await call<{ temporary_password: string }>({ method: 'POST', path: `/api/admin/users/${resetting.id}/reset-password` })
                    setTemporary({ scholar: resetting, password: result.temporary_password })
                    setResetting(null)
                    await list.reload()
                  })
                }
              >
                Reset password
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">
            They are signed out everywhere and get a temporary password to replace at their next sign-in. Their saved items are not touched.
          </p>
        </Modal>
      )}

      {temporary && (
        <Modal title={`Temporary password for ${temporary.scholar.email}`} onClose={() => setTemporary(null)} footer={<Button variant="primary" onClick={() => setTemporary(null)}>I have given it to them</Button>}>
          <Alert tone="amber">Shown once. Give it to the scholar in person or by a channel you trust.</Alert>
          <div className="mt-3 flex items-center gap-2">
            <code className="flex-1 break-all rounded bg-slate-100 p-2 font-mono text-sm" data-testid="temporary-password">{temporary.password}</code>
            <Button icon={<Copy className="h-4 w-4" />} onClick={() => osiss.copyText(temporary.password)}>
              Copy
            </Button>
          </div>
        </Modal>
      )}

      {deleting && (
        <Modal
          title={`Delete ${deleting.email}?`}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button onClick={() => setDeleting(null)}>Cancel</Button>
              <Button
                variant="danger"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    await call({ method: 'DELETE', path: `/api/admin/users/${deleting.id}` })
                    setDeleting(null)
                    await list.reload()
                  })
                }
              >
                Delete account
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">
            Removes the account and everything the scholar saved: {deleting.evidence_count} saved items and {deleting.note_count} notes. This cannot be undone. To block sign-in without deleting, switch the account off instead.
          </p>
        </Modal>
      )}
    </div>
  )
}

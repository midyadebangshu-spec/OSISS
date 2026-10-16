import { KeyRound, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState } from 'react'

import type { VaultStatus } from '../../shared/types.ts'
import { Alert, Badge, Button, Card, Field, Input, Messages, Modal, PageHeader } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { useAction } from '../lib/hooks.ts'

export function Security({ vault, onChange }: { vault: VaultStatus; onChange: () => void }) {
  const [current, setCurrent] = useState('')
  const [kind, setKind] = useState<'code' | 'passphrase'>('code')
  const [passphrase, setPassphrase] = useState('')
  const [repeat, setRepeat] = useState('')
  const [newCode, setNewCode] = useState<string | null>(null)
  const [autoLock, setAutoLock] = useState(vault.autoLockMinutes)
  const [confirmReset, setConfirmReset] = useState(false)
  const { busy, error, notice, run, setError, setNotice } = useAction()

  const change = () =>
    run(async () => {
      if (kind === 'passphrase' && passphrase !== repeat) throw new Error('The two passphrases differ.')
      const result = await osiss.vault.changePasskey(current, kind === 'code' ? { kind } : { kind, passphrase })
      if (!result.ok) throw new Error(result.error)
      setCurrent('')
      setPassphrase('')
      setRepeat('')
      if (result.passkey) setNewCode(result.passkey)
      onChange()
    }, kind === 'passphrase' ? 'Passphrase set. Use it the next time you unlock.' : undefined)

  return (
    <div className="space-y-5">
      <PageHeader title="App security" description="How this app is protected on this computer. Server access is protected separately by each server's ADMIN_TOKEN." />
      <Card title="Protection">
        <ul className="space-y-2 text-sm">
          <li className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-600" />
            Saved servers and tokens are encrypted (AES-256-GCM) with a key derived from your {vault.passkeyKind === 'passphrase' ? 'passphrase' : '6-character passkey'} (scrypt).
          </li>
          <li className="flex items-center gap-2">
            {vault.osProtected ? <ShieldCheck className="h-4 w-4 text-emerald-600" /> : <ShieldAlert className="h-4 w-4 text-amber-600" />}
            OS keychain sealing: {vault.osProtected ? <Badge tone="green">on</Badge> : <Badge tone="amber">not available on this system</Badge>}
          </li>
          <li className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-600" />
            After 5 wrong passkeys the app locks for 30 s, doubling each time (up to 15 min).
          </li>
        </ul>
        {!vault.osProtected && vault.passkeyKind !== 'passphrase' && (
          <div className="mt-3">
            <Alert tone="amber">Without the OS keychain, someone who copies this computer's app data could try all 6-character codes offline. A passphrase is much stronger.</Alert>
          </div>
        )}
      </Card>

      <Card title="Change passkey">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label={`Current ${vault.passkeyKind === 'passphrase' ? 'passphrase' : 'passkey'}`}>
            <Input type="password" value={current} onChange={(event) => setCurrent(event.target.value)} autoComplete="off" />
          </Field>
          <Field label="New">
            <select className="w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm" value={kind} onChange={(event) => setKind(event.target.value as 'code' | 'passphrase')}>
              <option value="code">A new random 6-character passkey</option>
              <option value="passphrase">A passphrase I choose (10+ characters)</option>
            </select>
          </Field>
          {kind === 'passphrase' && (
            <>
              <Field label="New passphrase">
                <Input type="password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} autoComplete="new-password" />
              </Field>
              <Field label="Repeat it">
                <Input type="password" value={repeat} onChange={(event) => setRepeat(event.target.value)} autoComplete="new-password" />
              </Field>
            </>
          )}
        </div>
        <div className="mt-3 space-y-3">
          <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
          <Button variant="primary" icon={<KeyRound className="h-4 w-4" />} busy={busy} disabled={!current || (kind === 'passphrase' && passphrase.length < 10)} onClick={change}>
            Change
          </Button>
        </div>
      </Card>

      <Card title="Auto-lock">
        <div className="flex items-end gap-3">
          <Field label="Lock after this many idle minutes">
            <div className="w-28">
              <Input type="number" min={1} max={240} value={autoLock} onChange={(event) => setAutoLock(Number(event.target.value))} />
            </div>
          </Field>
          <Button
            busy={busy}
            disabled={autoLock === vault.autoLockMinutes || autoLock < 1}
            onClick={() =>
              run(async () => {
                await osiss.vault.setAutoLock(autoLock)
                onChange()
              }, 'Saved.')
            }
          >
            Save
          </Button>
        </div>
      </Card>

      <Card title="Reset">
        <p className="mb-3 text-sm text-slate-600">Deletes all saved servers on this computer and asks for a new passkey. The servers are not changed.</p>
        <Button variant="danger" onClick={() => setConfirmReset(true)}>
          Reset app…
        </Button>
      </Card>

      {newCode && (
        <Modal title="Your new passkey" footer={<Button variant="primary" onClick={() => setNewCode(null)}>I have written it down</Button>}>
          <p className="mb-3 text-sm text-slate-600">It is shown only once. The old passkey no longer works.</p>
          <div className="rounded-lg border-2 border-dashed border-neutral-400 bg-neutral-100 py-4 text-center font-mono text-3xl font-bold tracking-[0.3em] text-black">
            {newCode.slice(0, 3)}-{newCode.slice(3)}
          </div>
        </Modal>
      )}
      {confirmReset && (
        <Modal
          title="Reset the app?"
          onClose={() => setConfirmReset(false)}
          footer={
            <>
              <Button onClick={() => setConfirmReset(false)}>Cancel</Button>
              <Button
                variant="danger"
                onClick={async () => {
                  await osiss.vault.reset()
                  onChange()
                }}
              >
                Reset
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">All saved servers and tokens on this computer are deleted.</p>
        </Modal>
      )}
    </div>
  )
}

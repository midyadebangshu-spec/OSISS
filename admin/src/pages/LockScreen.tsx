import { KeyRound, Lock, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'

import type { VaultStatus } from '../../shared/types.ts'
import { Alert, Button, Input, Modal } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'

function formatCode(code: string) {
  return `${code.slice(0, 3)}-${code.slice(3)}`
}

function useCountdown(until: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (until <= Date.now()) return
    const timer = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(timer)
  }, [until])
  return Math.max(0, Math.ceil((until - now) / 1000))
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-100 to-indigo-50 p-6">
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-lg">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600 text-white">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <div className="text-lg font-semibold">OSISS Admin</div>
            <div className="text-xs text-slate-500">Server control for the OSISS search engine</div>
          </div>
        </div>
        {children}
      </div>
    </div>
  )
}

function Setup({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState<string | null>(null)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const confirm = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    const result = await osiss.vault.confirmSetup(typed)
    setBusy(false)
    if (result.ok) onDone()
    else setError(result.error)
  }

  if (!code) {
    return (
      <Frame>
        <h1 className="mb-2 text-base font-semibold">Welcome</h1>
        <p className="mb-4 text-sm text-slate-600">
          This app controls your OSISS server, so it is locked with a passkey. The app will now create a random
          6-character passkey for this computer. You will need it every time you open the app.
        </p>
        <Button variant="primary" className="w-full" icon={<KeyRound className="h-4 w-4" />} onClick={async () => setCode((await osiss.vault.beginSetup()).passkey)}>
          Create my passkey
        </Button>
      </Frame>
    )
  }

  return (
    <Frame>
      <h1 className="mb-2 text-base font-semibold">Your passkey</h1>
      <p className="mb-3 text-sm text-slate-600">Write it down and keep it somewhere safe. It is shown only once.</p>
      <div className="mb-4 rounded-lg border-2 border-dashed border-indigo-300 bg-indigo-50 py-4 text-center font-mono text-3xl font-bold tracking-[0.3em] text-indigo-800" data-testid="new-passkey">
        {formatCode(code)}
      </div>
      <form onSubmit={confirm} className="space-y-3">
        <label className="block text-sm font-medium text-slate-700">
          Type it back to confirm
          <Input autoFocus className="mt-1 font-mono uppercase tracking-widest" value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="ABC-123" aria-label="Confirm passkey" />
        </label>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" variant="primary" className="w-full" busy={busy} disabled={!typed.trim()}>
          Confirm and continue
        </Button>
      </form>
    </Frame>
  )
}

function Unlock({ status, onDone }: { status: VaultStatus; onDone: () => void }) {
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [lockedUntil, setLockedUntil] = useState(status.lockedUntil)
  const [busy, setBusy] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const wait = useCountdown(lockedUntil)

  const unlock = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    const result = await osiss.vault.unlock(typed)
    setBusy(false)
    if (result.ok) return onDone()
    setError(result.error)
    setLockedUntil(result.lockedUntil)
    setTyped('')
  }

  const isCode = status.passkeyKind !== 'passphrase'
  return (
    <Frame>
      <h1 className="mb-1 flex items-center gap-2 text-base font-semibold">
        <Lock className="h-4 w-4" /> Locked
      </h1>
      <p className="mb-4 text-sm text-slate-600">Enter your {isCode ? '6-character passkey' : 'passphrase'} to open the app.</p>
      <form onSubmit={unlock} className="space-y-3">
        <Input
          autoFocus
          type={isCode ? 'text' : 'password'}
          className={isCode ? 'text-center font-mono text-lg uppercase tracking-[0.3em]' : ''}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder={isCode ? 'ABC-123' : 'Passphrase'}
          aria-label="Passkey"
          disabled={wait > 0}
          autoComplete="off"
        />
        {wait > 0 ? (
          <Alert tone="amber">Too many wrong attempts. Try again in {wait} s.</Alert>
        ) : (
          error && <Alert>{error}</Alert>
        )}
        <Button type="submit" variant="primary" className="w-full" busy={busy} disabled={!typed.trim() || wait > 0}>
          Unlock
        </Button>
      </form>
      <button type="button" className="mt-4 text-xs text-slate-500 underline hover:text-slate-700" onClick={() => setConfirmReset(true)}>
        Forgot the passkey?
      </button>
      {!status.osProtected && (
        <p className="mt-3 text-xs text-slate-400">The OS keychain is not available, so the vault is protected by the passkey alone.</p>
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
                  onDone()
                }}
              >
                Reset and create a new passkey
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">
            This deletes the saved servers on this computer and creates a new passkey. Nothing on the servers changes;
            you only need to add them again with their admin token (ADMIN_TOKEN in the server's .env file).
          </p>
        </Modal>
      )}
    </Frame>
  )
}

export function LockScreen({ status, onChange }: { status: VaultStatus; onChange: () => void }) {
  return status.initialized ? <Unlock status={status} onDone={onChange} /> : <Setup onDone={onChange} />
}

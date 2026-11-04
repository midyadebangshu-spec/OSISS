import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Download, KeyRound, LogOut } from 'lucide-react'
import { useAuth } from '../lib/auth'
import { changePassword, exportWorkspace, login, logout, register, registrationOpen } from '../lib/workspaceApi'
import { workspaceHref } from '../lib/route'

const FIELD = 'w-full border-2 border-zinc-950 px-3 py-2 text-sm outline-none focus:bg-zinc-50'
const BUTTON = 'border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white disabled:opacity-40'

function SignInForm() {
    const { setUser } = useAuth()
    const [mode, setMode] = useState<'login' | 'register'>('login')
    const [canRegister, setCanRegister] = useState(true)
    const [email, setEmail] = useState('')
    const [name, setName] = useState('')
    const [password, setPassword] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        void registrationOpen().then(setCanRegister)
    }, [])

    const submit = async (event: FormEvent) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        try {
            setUser(mode === 'login' ? await login(email, password) : await register(email, password, name))
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : 'Something went wrong.')
        } finally {
            setBusy(false)
        }
    }

    const registering = mode === 'register' && canRegister
    return (
        <section className="mx-auto max-w-md space-y-5 py-8">
            <header className="space-y-1">
                <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Your workspace</p>
                <h1 className="text-2xl font-semibold tracking-tight">{registering ? 'Create an account' : 'Sign in'}</h1>
                <p className="text-sm text-zinc-600">Save passages, build collections and take notes. Search stays open without an account.</p>
            </header>
            <form onSubmit={(event) => void submit(event)} className="space-y-3">
                {registering && (
                    <label className="block text-sm">
                        Name (optional)
                        <input className={FIELD} value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" />
                    </label>
                )}
                <label className="block text-sm">
                    Email
                    <input className={FIELD} type="email" required value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
                </label>
                <label className="block text-sm">
                    Password
                    <input
                        className={FIELD}
                        type="password"
                        required
                        minLength={registering ? 8 : undefined}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        autoComplete={registering ? 'new-password' : 'current-password'}
                    />
                    {registering && <span className="text-xs text-zinc-500">At least 8 characters.</span>}
                </label>
                {error && <p role="alert" className="border border-red-700 px-3 py-2 text-sm text-red-700">{error}</p>}
                <button type="submit" disabled={busy} className={BUTTON}>
                    {busy ? 'Please wait…' : registering ? 'Create account' : 'Sign in'}
                </button>
            </form>
            {canRegister ? (
                <p className="text-sm text-zinc-600">
                    {registering ? 'Already have an account? ' : 'New here? '}
                    <button type="button" className="underline underline-offset-4" onClick={() => { setMode(registering ? 'login' : 'register'); setError(null) }}>
                        {registering ? 'Sign in' : 'Create an account'}
                    </button>
                </p>
            ) : (
                <p className="text-sm text-zinc-600">New accounts are created by the administrator.</p>
            )}
            {!registering && <p className="text-sm text-zinc-500">Forgot your password? Ask the administrator to reset it.</p>}
        </section>
    )
}

function PasswordForm({ forced }: { forced: boolean }) {
    const { user, setUser } = useAuth()
    const [current, setCurrent] = useState('')
    const [next, setNext] = useState('')
    const [busy, setBusy] = useState(false)
    const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

    const submit = async (event: FormEvent) => {
        event.preventDefault()
        setBusy(true)
        setMessage(null)
        try {
            await changePassword(current, next)
            setCurrent('')
            setNext('')
            setMessage({ ok: true, text: 'Password changed.' })
            if (user) setUser({ ...user, must_change_password: false })
        } catch (failure) {
            setMessage({ ok: false, text: failure instanceof Error ? failure.message : 'Could not change the password.' })
        } finally {
            setBusy(false)
        }
    }

    return (
        <form onSubmit={(event) => void submit(event)} className="space-y-3 border-2 border-zinc-950 p-4">
            <h2 className="flex items-center gap-2 font-semibold">
                <KeyRound className="h-4 w-4" /> {forced ? 'Choose a new password' : 'Change password'}
            </h2>
            {forced && <p className="text-sm text-zinc-600">The administrator gave you a temporary password. Choose your own to continue.</p>}
            <label className="block text-sm">
                {forced ? 'Temporary password' : 'Current password'}
                <input className={FIELD} type="password" required value={current} onChange={(event) => setCurrent(event.target.value)} autoComplete="current-password" />
            </label>
            <label className="block text-sm">
                New password
                <input className={FIELD} type="password" required minLength={8} value={next} onChange={(event) => setNext(event.target.value)} autoComplete="new-password" />
            </label>
            {message && <p role={message.ok ? 'status' : 'alert'} className={`text-sm ${message.ok ? 'text-green-800' : 'text-red-700'}`}>{message.text}</p>}
            <button type="submit" disabled={busy || !current || next.length < 8} className={BUTTON}>Change password</button>
        </form>
    )
}

export function AccountPage() {
    const { user, setUser } = useAuth()

    if (user === undefined) return <p className="py-8 text-sm text-zinc-500">Checking your session…</p>
    if (user === null) return <SignInForm />

    const signOut = async () => {
        try {
            await logout()
        } finally {
            setUser(null)
        }
    }

    const download = async () => {
        const data = await exportWorkspace()
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
        const link = document.createElement('a')
        link.href = url
        link.download = 'osiss-workspace.json'
        link.click()
        URL.revokeObjectURL(url)
    }

    return (
        <section className="mx-auto max-w-xl space-y-6 py-6">
            <header className="space-y-1 border-b-2 border-zinc-950 pb-4">
                <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Account</p>
                <h1 className="text-2xl font-semibold tracking-tight">{user.name || user.email}</h1>
                {user.name && <p className="text-sm text-zinc-600">{user.email}</p>}
            </header>
            <PasswordForm forced={user.must_change_password} />
            {!user.must_change_password && (
                <div className="flex flex-wrap items-center gap-4 text-sm">
                    <a href={workspaceHref()} className="underline underline-offset-4">Open your workspace</a>
                    <button type="button" onClick={() => void download()} className="inline-flex items-center gap-1 underline underline-offset-4">
                        <Download className="h-4 w-4" /> Download everything (JSON)
                    </button>
                </div>
            )}
            <button type="button" onClick={() => void signOut()} className="inline-flex items-center gap-1 border-2 border-zinc-950 px-4 py-2 text-sm font-medium hover:bg-zinc-100">
                <LogOut className="h-4 w-4" /> Sign out
            </button>
        </section>
    )
}

import {
  Activity,
  BookOpen,
  Gauge,
  Globe,
  Lock,
  Search,
  Server,
  Settings as SettingsIcon,
  ShieldCheck,
  Workflow,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import type { ApiRequest, Profile, VaultStatus } from '../shared/types.ts'
import { AccessWizard } from './components/AccessWizard.tsx'
import { Badge, Spinner } from './components/ui.tsx'
import { osiss } from './lib/bridge.ts'
import { callApi, errorText, ServerContext } from './lib/server.ts'
import type { Access } from './lib/types.ts'
import { AccessPage } from './pages/Access.tsx'
import { Books } from './pages/Books.tsx'
import { Dashboard } from './pages/Dashboard.tsx'
import { Ingestion } from './pages/Ingestion.tsx'
import { SearchConsole } from './pages/SearchConsole.tsx'
import { Security } from './pages/Security.tsx'
import { Servers } from './pages/Servers.tsx'
import { SettingsPage } from './pages/Settings.tsx'

type PageKey = 'dashboard' | 'books' | 'ingestion' | 'search' | 'settings' | 'access' | 'servers' | 'security'

const SERVER_PAGES: Array<{ key: PageKey; label: string; icon: ReactNode }> = [
  { key: 'dashboard', label: 'Dashboard', icon: <Gauge className="h-4 w-4" /> },
  { key: 'books', label: 'Books', icon: <BookOpen className="h-4 w-4" /> },
  { key: 'ingestion', label: 'Ingestion & jobs', icon: <Workflow className="h-4 w-4" /> },
  { key: 'search', label: 'Search console', icon: <Search className="h-4 w-4" /> },
  { key: 'settings', label: 'Search settings', icon: <SettingsIcon className="h-4 w-4" /> },
  { key: 'access', label: 'Access & API keys', icon: <Globe className="h-4 w-4" /> },
]

const APP_PAGES: Array<{ key: PageKey; label: string; icon: ReactNode }> = [
  { key: 'servers', label: 'Servers', icon: <Server className="h-4 w-4" /> },
  { key: 'security', label: 'App security', icon: <ShieldCheck className="h-4 w-4" /> },
]

const LAST_PROFILE_KEY = 'osiss-admin:last-profile'

function readLastProfile(): string | null {
  try {
    return localStorage.getItem(LAST_PROFILE_KEY)
  } catch {
    return null
  }
}

function useIdleLock(minutes: number, onLock: () => void) {
  useEffect(() => {
    let last = Date.now()
    const touch = () => {
      last = Date.now()
    }
    const events = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'] as const
    events.forEach((name) => window.addEventListener(name, touch, { passive: true }))
    const timer = setInterval(() => {
      if (Date.now() - last > minutes * 60_000) onLock()
    }, 15_000)
    return () => {
      events.forEach((name) => window.removeEventListener(name, touch))
      clearInterval(timer)
    }
  }, [minutes, onLock])
}

export function Shell({ vault, onLock, onVaultChange }: { vault: VaultStatus; onLock: () => void; onVaultChange: () => void }) {
  const [profiles, setProfiles] = useState<Profile[] | null>(null)
  const [profileId, setProfileId] = useState<string | null>(readLastProfile)
  const [page, setPage] = useState<PageKey>('dashboard')
  const [access, setAccess] = useState<Access | null>(null)
  const [connection, setConnection] = useState<{ ok: boolean; message?: string } | null>(null)
  const [wizardDismissed, setWizardDismissed] = useState<Record<string, boolean>>({})

  useIdleLock(vault.autoLockMinutes, onLock)

  const loadProfiles = useCallback(async () => {
    const list = await osiss.profiles.list()
    setProfiles(list)
    return list
  }, [])

  useEffect(() => {
    void osiss.profiles.list().then(setProfiles)
  }, [])

  const profile = useMemo(() => profiles?.find((item) => item.id === profileId) ?? profiles?.[0] ?? null, [profiles, profileId])

  const checkConnection = useCallback(async (target: Profile) => {
    try {
      setAccess(await callApi<Access>(target.id, { path: '/api/admin/access' }))
      setConnection({ ok: true })
    } catch (error) {
      setAccess(null)
      setConnection({ ok: false, message: errorText(error) })
    }
  }, [])

  useEffect(() => {
    if (!profile) return
    try {
      localStorage.setItem(LAST_PROFILE_KEY, profile.id)
    } catch {
      /* per-viewer convenience only */
    }
    const first = setTimeout(() => void checkConnection(profile), 0)
    const timer = setInterval(() => void checkConnection(profile), 20_000)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [profile, checkConnection])

  const context = useMemo(
    () => (profile ? { profile, call: <T,>(request: ApiRequest) => callApi<T>(profile.id, request) } : null),
    [profile],
  )

  if (!profiles) return <Spinner label="Loading…" />

  const activePage: PageKey = !profile && page !== 'security' ? 'servers' : page
  const showWizard = Boolean(
    profile && access && !access.configured && !wizardDismissed[profile.id] && activePage !== 'servers' && activePage !== 'security',
  )

  const navButton = (item: { key: PageKey; label: string; icon: ReactNode }, disabled = false) => (
    <button
      key={item.key}
      type="button"
      disabled={disabled}
      onClick={() => setPage(item.key)}
      className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition disabled:opacity-40 ${
        activePage === item.key ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-600 hover:bg-slate-100'
      }`}
    >
      {item.icon}
      {item.label}
    </button>
  )

  let content: ReactNode
  if (activePage === 'servers') {
    content = (
      <Servers
        profiles={profiles}
        activeId={profile?.id ?? null}
        onChanged={async (selectId) => {
          // Stay on this page while the admin is still filling in the form (the first profile would otherwise
          // switch the view to the Dashboard).
          setPage('servers')
          const list = await loadProfiles()
          if (selectId) setProfileId(selectId)
          else if (!list.some((item) => item.id === profileId)) setProfileId(list[0]?.id ?? null)
        }}
        onSelect={(id) => {
          setProfileId(id)
          setPage('dashboard')
        }}
      />
    )
  } else if (activePage === 'security') {
    content = <Security vault={vault} onChange={onVaultChange} />
  } else if (context) {
    const pages: Record<string, ReactNode> = {
      dashboard: <Dashboard />,
      books: <Books />,
      ingestion: <Ingestion />,
      search: <SearchConsole />,
      settings: <SettingsPage />,
      access: <AccessPage onChange={() => profile && checkConnection(profile)} />,
    }
    content = pages[activePage]
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-indigo-600 text-white">
            <Activity className="h-4 w-4" />
          </div>
          <div className="text-sm font-semibold">OSISS Admin</div>
        </div>
        <div className="border-b border-slate-100 p-3">
          <label className="mb-1 block text-xs font-medium text-slate-500" htmlFor="server-select">
            Server
          </label>
          <select
            id="server-select"
            className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
            value={profile?.id ?? ''}
            onChange={(event) => setProfileId(event.target.value)}
            disabled={!profiles.length}
          >
            {!profiles.length && <option value="">No servers yet</option>}
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          {profile && connection && (
            <div className="mt-2 flex items-center gap-2 text-xs" title={connection.message}>
              <span className={`h-2 w-2 rounded-full ${connection.ok ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span className="truncate text-slate-500">{connection.ok ? 'Connected' : connection.message}</span>
            </div>
          )}
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {SERVER_PAGES.map((item) => navButton(item, !profile))}
          <div className="my-3 border-t border-slate-100" />
          {APP_PAGES.map((item) => navButton(item))}
        </nav>
        <div className="border-t border-slate-100 p-3">
          <button
            type="button"
            onClick={onLock}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            <Lock className="h-4 w-4" /> Lock now
          </button>
          <div className="mt-2 text-center text-[11px] text-slate-400">Auto-locks after {vault.autoLockMinutes} min idle</div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl p-6">
          {profile && connection && !connection.ok && activePage !== 'servers' && activePage !== 'security' && (
            <div className="mb-4">
              <Badge tone="red">Not connected</Badge> <span className="text-sm text-slate-600">{connection.message}</span>
            </div>
          )}
          {/* Always rendered, so adding the first server does not remount (and reset) the page. */}
          <ServerContext.Provider value={context}>{content}</ServerContext.Provider>
        </div>
      </main>
      {showWizard && profile && (
        <AccessWizard
          profileId={profile.id}
          serverName={profile.name}
          onDone={async () => {
            setWizardDismissed((current) => ({ ...current, [profile.id]: true }))
            await checkConnection(profile)
          }}
          onLater={() => setWizardDismissed((current) => ({ ...current, [profile.id]: true }))}
        />
      )}
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'

import type { VaultStatus } from '../shared/types.ts'
import { Spinner } from './components/ui.tsx'
import { osiss } from './lib/bridge.ts'
import { LockScreen } from './pages/LockScreen.tsx'
import { Shell } from './Shell.tsx'

export default function App() {
  const [status, setStatus] = useState<VaultStatus | null>(null)

  const refresh = useCallback(async () => setStatus(await osiss.vault.status()), [])

  useEffect(() => {
    void osiss.vault.status().then(setStatus)
  }, [])

  if (!status) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner label="Starting…" />
      </div>
    )
  }
  if (!status.unlocked) return <LockScreen status={status} onChange={refresh} />
  return (
    <Shell
      vault={status}
      onLock={async () => {
        await osiss.vault.lock()
        await refresh()
      }}
      onVaultChange={refresh}
    />
  )
}

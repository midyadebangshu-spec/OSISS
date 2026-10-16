import { useCallback, useEffect, useRef, useState } from 'react'

import { errorText } from './server.ts'

export type Loaded<T> = {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => Promise<void>
}

/** Load data with `load`, again every `intervalMs` (if given) and whenever `reload` is called. */
export function useLoader<T>(load: () => Promise<T>, intervalMs?: number, deps: unknown[] = []): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  const alive = useRef(true)

  useEffect(() => {
    loadRef.current = load
  })

  const reload = useCallback(async () => {
    try {
      const value = await loadRef.current()
      if (!alive.current) return
      setData(value)
      setError(null)
    } catch (caught) {
      if (alive.current) setError(errorText(caught))
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    const first = setTimeout(() => void reload(), 0)
    const timer = intervalMs ? setInterval(() => void reload(), intervalMs) : undefined
    return () => {
      alive.current = false
      clearTimeout(first)
      if (timer) clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, intervalMs, ...deps])

  return { data, error, loading, reload }
}

/** Run an async action, tracking busy state and the last error / success message. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const run = useCallback(async <T>(action: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await action()
      if (success) setNotice(success)
      return result
    } catch (caught) {
      setError(errorText(caught))
      return undefined
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, error, notice, run, setError, setNotice }
}

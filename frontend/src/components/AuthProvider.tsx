import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { AuthContext } from '../lib/auth'
import { fetchMe } from '../lib/workspaceApi'
import type { User } from '../types/workspace'

/** Finds out once whether the browser holds a valid session, and shares the signed-in scholar with the pages. */
export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null | undefined>(undefined)

    useEffect(() => {
        const controller = new AbortController()
        fetchMe(controller.signal)
            .then(setUser)
            .catch(() => {
                if (!controller.signal.aborted) setUser(null)
            })
        return () => controller.abort()
    }, [])

    const value = useMemo(() => ({ user, setUser }), [user])
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

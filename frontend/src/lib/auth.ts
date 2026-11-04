import { createContext, useContext } from 'react'
import type { User } from '../types/workspace'

export type Auth = {
    /** undefined while the first check runs, null when signed out. */
    user: User | null | undefined
    setUser: (user: User | null) => void
}

export const AuthContext = createContext<Auth>({ user: null, setUser: () => undefined })

export function useAuth(): Auth {
    return useContext(AuthContext)
}

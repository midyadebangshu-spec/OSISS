import { createContext, useContext } from 'react'

import type { ApiRequest, Profile } from '../../shared/types.ts'
import { osiss } from './bridge.ts'

export class ApiError extends Error {
  readonly status: number
  readonly retryAfter?: number

  constructor(message: string, status: number, retryAfter?: number) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

/** Call the admin API of a profile; resolves with the JSON body or throws ApiError. */
export async function callApi<T>(profileId: string, request: ApiRequest): Promise<T> {
  const response = await osiss.api.request<T>(profileId, request)
  if (!response.ok) throw new ApiError(response.error, response.status, response.retryAfter)
  return response.data
}

export type ServerContextValue = {
  profile: Profile
  call: <T>(request: ApiRequest) => Promise<T>
}

export const ServerContext = createContext<ServerContextValue | null>(null)

export function useServer(): ServerContextValue {
  const value = useContext(ServerContext)
  if (!value) throw new Error('useServer outside a connected server')
  return value
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'The server rejected the admin token. Update it under Servers.'
    if (error.status === 429) return `Too many wrong tokens from this address; retry in ${error.retryAfter ?? 'a few'} s.`
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}

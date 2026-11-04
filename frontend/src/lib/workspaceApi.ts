import type { Collection, Dashboard, Evidence, EvidenceDraft, Note, User } from '../types/workspace'

/** Thrown for a non-2xx answer; `status` lets callers tell "sign in" (401) from other failures. */
export class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
        super(message)
        this.status = status
    }
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(path, {
        method,
        signal,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) {
        let detail = ''
        try {
            const data = (await response.json()) as { detail?: unknown }
            detail = typeof data.detail === 'string' ? data.detail : ''
        } catch {
            detail = ''
        }
        throw new ApiError(detail || `Request failed with status ${response.status}`, response.status)
    }
    return (await response.json()) as T
}

const get = <T,>(path: string, signal?: AbortSignal) => request<T>('GET', path, undefined, signal)

// --- accounts -------------------------------------------------------------------------------------------------

export async function fetchMe(signal?: AbortSignal): Promise<User | null> {
    try {
        return (await get<{ user: User }>('/api/auth/me', signal)).user
    } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
    }
}

export async function registrationOpen(): Promise<boolean> {
    try {
        return (await get<{ registration: boolean }>('/api/auth/config')).registration
    } catch {
        return true
    }
}

export async function login(email: string, password: string): Promise<User> {
    return (await request<{ user: User }>('POST', '/api/auth/login', { email, password })).user
}

export async function register(email: string, password: string, name: string): Promise<User> {
    return (await request<{ user: User }>('POST', '/api/auth/register', { email, password, name })).user
}

export async function logout(): Promise<void> {
    await request('POST', '/api/auth/logout')
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
    await request('POST', '/api/auth/password', { current_password: currentPassword, new_password: newPassword })
}

// --- workspace ------------------------------------------------------------------------------------------------

export const getDashboard = (signal?: AbortSignal) => get<Dashboard>('/api/workspace/dashboard', signal)

/** Remember a search or an opened book for the dashboard. Failures are ignored: it is a convenience. */
export function recordActivity(kind: 'search' | 'book', ref: string, bookId?: number): void {
    void request('POST', '/api/workspace/activity', { kind, ref, book_id: bookId ?? null }).catch(() => undefined)
}

export const saveEvidence = (draft: EvidenceDraft) => request<Evidence>('POST', '/api/workspace/evidence', draft)

export const deleteEvidence = (id: number) => request('DELETE', `/api/workspace/evidence/${id}`)

export const listEvidence = (collectionId?: number, signal?: AbortSignal) =>
    get<{ total: number; items: Evidence[] }>(
        `/api/workspace/evidence?limit=200${collectionId ? `&collection_id=${collectionId}` : ''}`,
        signal,
    )

export async function listCollections(signal?: AbortSignal): Promise<Collection[]> {
    return (await get<{ collections: Collection[] }>('/api/workspace/collections', signal)).collections
}

export const createCollection = (name: string, description = '') =>
    request<Collection>('POST', '/api/workspace/collections', { name, description })

export const getCollection = (id: number, signal?: AbortSignal) =>
    get<Collection & { items: Evidence[] }>(`/api/workspace/collections/${id}`, signal)

export const updateCollection = (id: number, fields: { name?: string; description?: string }) =>
    request<Collection>('PATCH', `/api/workspace/collections/${id}`, fields)

export const deleteCollection = (id: number) => request('DELETE', `/api/workspace/collections/${id}`)

export const addToCollection = (collectionId: number, evidenceId: number) =>
    request('PUT', `/api/workspace/collections/${collectionId}/items/${evidenceId}`)

export const removeFromCollection = (collectionId: number, evidenceId: number) =>
    request('DELETE', `/api/workspace/collections/${collectionId}/items/${evidenceId}`)

export async function listNotes(options: { q?: string; concept?: string } = {}, signal?: AbortSignal): Promise<Note[]> {
    const params = new URLSearchParams()
    if (options.q) params.set('q', options.q)
    if (options.concept) params.set('concept', options.concept)
    return (await get<{ notes: Note[] }>(`/api/workspace/notes?${params}`, signal)).notes
}

export const createNote = (title: string, body: string, evidenceId?: number) =>
    request<Note>('POST', '/api/workspace/notes', { title, body, evidence_id: evidenceId ?? null })

export const updateNote = (id: number, fields: { title?: string; body?: string }) =>
    request<Note>('PATCH', `/api/workspace/notes/${id}`, fields)

export const deleteNote = (id: number) => request('DELETE', `/api/workspace/notes/${id}`)

export const exportWorkspace = () => get<unknown>('/api/workspace/export')

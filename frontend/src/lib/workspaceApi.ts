import type {
    AppNotification, Board, BoardKind, BoardNode, BoardEdge, BoardRelation, Card, CardGrade, SavedSearch, StudyStats,
    CitationStyle, Collection, Dashboard, Draft, DraftCheck, DraftSummary, Evidence, EvidenceDraft, Note, Project,
    ProjectDetail, References, RenderedDraft, User,
} from '../types/workspace'

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
export function recordActivity(kind: 'search' | 'book', ref: string, bookId?: number, pageNumber?: number): void {
    void request('POST', '/api/workspace/activity', {
        kind,
        ref,
        book_id: bookId ?? null,
        page_number: pageNumber ?? null,
    }).catch(() => undefined)
}

/** The scholar's own highlights on one page of a book. */
export async function listHighlights(bookId: number, page: number, signal?: AbortSignal): Promise<Evidence[]> {
    return (
        await get<{ items: Evidence[] }>(
            `/api/workspace/evidence?kind=highlight&book_id=${bookId}&page_number=${page}&limit=200`,
            signal,
        )
    ).items
}

export const saveEvidence = (draft: EvidenceDraft) => request<Evidence>('POST', '/api/workspace/evidence', draft)

export const deleteEvidence = (id: number) => request('DELETE', `/api/workspace/evidence/${id}`)

export const listEvidence = (options: { collectionId?: number; projectId?: number } = {}, signal?: AbortSignal) =>
    get<{ total: number; items: Evidence[] }>(
        `/api/workspace/evidence?limit=200${options.collectionId ? `&collection_id=${options.collectionId}` : ''}${
            options.projectId ? `&project_id=${options.projectId}` : ''
        }`,
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

export async function listNotes(
    options: { q?: string; concept?: string; projectId?: number } = {},
    signal?: AbortSignal,
): Promise<Note[]> {
    const params = new URLSearchParams()
    if (options.q) params.set('q', options.q)
    if (options.concept) params.set('concept', options.concept)
    if (options.projectId) params.set('project_id', String(options.projectId))
    return (await get<{ notes: Note[] }>(`/api/workspace/notes?${params}`, signal)).notes
}

export const createNote = (title: string, body: string, evidenceId?: number, projectId?: number) =>
    request<Note>('POST', '/api/workspace/notes', { title, body, evidence_id: evidenceId ?? null, project_id: projectId ?? null })

export const updateNote = (id: number, fields: { title?: string; body?: string }) =>
    request<Note>('PATCH', `/api/workspace/notes/${id}`, fields)

export const deleteNote = (id: number) => request('DELETE', `/api/workspace/notes/${id}`)

export const exportWorkspace = () => get<unknown>('/api/workspace/export')

// --- projects -------------------------------------------------------------------------------------------------

export async function listProjects(signal?: AbortSignal): Promise<Project[]> {
    return (await get<{ projects: Project[] }>('/api/workspace/projects', signal)).projects
}

export const createProject = (title: string, question: string, bookIds: number[]) =>
    request<ProjectDetail>('POST', '/api/workspace/projects', { title, question, book_ids: bookIds })

export const getProject = (id: number, signal?: AbortSignal) => get<ProjectDetail>(`/api/workspace/projects/${id}`, signal)

export const updateProject = (id: number, fields: { title?: string; question?: string; description?: string }) =>
    request<ProjectDetail>('PATCH', `/api/workspace/projects/${id}`, fields)

export const deleteProject = (id: number) => request('DELETE', `/api/workspace/projects/${id}`)

export const addProjectBooks = (id: number, bookIds: number[]) =>
    request<ProjectDetail>('POST', `/api/workspace/projects/${id}/books`, { book_ids: bookIds })

export const removeProjectBook = (id: number, bookId: number) =>
    request('DELETE', `/api/workspace/projects/${id}/books/${bookId}`)

export const attachToProject = (projectId: number, evidenceId: number) =>
    request('PUT', `/api/workspace/projects/${projectId}/evidence/${evidenceId}`)

export const detachFromProject = (projectId: number, evidenceId: number) =>
    request('DELETE', `/api/workspace/projects/${projectId}/evidence/${evidenceId}`)

// --- writing studio and references ----------------------------------------------------------------------------

export async function listDrafts(signal?: AbortSignal): Promise<DraftSummary[]> {
    return (await get<{ drafts: DraftSummary[] }>('/api/workspace/drafts', signal)).drafts
}

export const createDraft = (title = '', body = '', style: CitationStyle = 'apa') =>
    request<Draft>('POST', '/api/workspace/drafts', { title, body, style })

export const getDraft = (id: number, signal?: AbortSignal) => get<Draft>(`/api/workspace/drafts/${id}`, signal)

export const updateDraft = (id: number, fields: { title?: string; body?: string; style?: CitationStyle; project_id?: number | null }) =>
    request<Draft>('PATCH', `/api/workspace/drafts/${id}`, fields)

export const deleteDraft = (id: number) => request('DELETE', `/api/workspace/drafts/${id}`)

export const renderDraft = (id: number, style?: CitationStyle, signal?: AbortSignal) =>
    get<RenderedDraft>(`/api/workspace/drafts/${id}/render${style ? `?style=${style}` : ''}`, signal)

export const checkDraft = (id: number) => request<DraftCheck>('POST', `/api/workspace/drafts/${id}/check`)

export function draftExportUrl(id: number, format: 'docx' | 'tex' | 'md', style: CitationStyle): string {
    return `/api/workspace/drafts/${id}/export?format=${format}&style=${style}`
}

export function getReferences(style: CitationStyle, projectId?: number, signal?: AbortSignal) {
    return get<References>(`/api/workspace/references?style=${style}${projectId ? `&project_id=${projectId}` : ''}`, signal)
}

export function referencesFileUrl(format: 'bibtex' | 'ris', projectId?: number): string {
    return `/api/workspace/references?format=${format}${projectId ? `&project_id=${projectId}` : ''}`
}

// --- research board -------------------------------------------------------------------------------------------

const boardBase = (projectId: number) => `/api/workspace/projects/${projectId}/board`

export const getBoard = (projectId: number, signal?: AbortSignal) => get<Board>(boardBase(projectId), signal)

export const addBoardNode = (
    projectId: number,
    node: { kind: BoardKind; text?: string; evidence_id?: number; note_id?: number; concept_key?: string; x?: number; y?: number },
) => request<BoardNode>('POST', `${boardBase(projectId)}/nodes`, node)

export const moveBoardNode = (projectId: number, nodeId: number, fields: { x?: number; y?: number; text?: string }) =>
    request<BoardNode>('PATCH', `${boardBase(projectId)}/nodes/${nodeId}`, fields)

export const deleteBoardNode = (projectId: number, nodeId: number) => request('DELETE', `${boardBase(projectId)}/nodes/${nodeId}`)

export const addBoardEdge = (projectId: number, sourceId: number, targetId: number, relation: BoardRelation, note = '') =>
    request<BoardEdge>('POST', `${boardBase(projectId)}/edges`, { source_id: sourceId, target_id: targetId, relation, note })

export const deleteBoardEdge = (projectId: number, edgeId: number) => request('DELETE', `${boardBase(projectId)}/edges/${edgeId}`)

export const seedBoardConcept = (projectId: number, conceptId: number) =>
    request<Board & { neighbours: number }>('POST', `${boardBase(projectId)}/seed`, { concept_id: conceptId })

export const boardOutline = (projectId: number) => request<Draft>('POST', `${boardBase(projectId)}/outline`)

// --- study: flashcards ----------------------------------------------------------------------------------------

export const createCard = (evidenceId: number, front?: string, projectId?: number) =>
    request<Card>('POST', '/api/workspace/cards', { evidence_id: evidenceId, front: front ?? null, project_id: projectId ?? null })

export const createCardsBulk = (target: { collection_id?: number; project_id?: number }) =>
    request<{ created: number; existing: number; skipped: number }>('POST', '/api/workspace/cards/bulk', target)

export const listCards = (projectId?: number, signal?: AbortSignal) =>
    get<{ total: number; cards: Card[] }>(`/api/workspace/cards?limit=500${projectId ? `&project_id=${projectId}` : ''}`, signal)

export const updateCard = (id: number, fields: { front?: string; suspended?: boolean }) =>
    request<Card>('PATCH', `/api/workspace/cards/${id}`, fields)

export const deleteCard = (id: number) => request('DELETE', `/api/workspace/cards/${id}`)

export const getDueCards = (projectId?: number, signal?: AbortSignal) =>
    get<{ cards: Card[]; stats: StudyStats }>(`/api/workspace/study/due?limit=20${projectId ? `&project_id=${projectId}` : ''}`, signal)

export const reviewCard = (id: number, grade: CardGrade) =>
    request<{ card: Card; stats: StudyStats }>('POST', `/api/workspace/cards/${id}/review`, { grade })

// --- saved searches and notifications -------------------------------------------------------------------------

export const saveSearch = (query: string, projectId?: number) =>
    request<SavedSearch>('POST', '/api/workspace/saved-searches', { query, project_id: projectId ?? null })

export const deleteSavedSearch = (id: number) => request('DELETE', `/api/workspace/saved-searches/${id}`)

export const checkSavedSearches = () =>
    request<{ checked: number; pending: number; notifications: AppNotification[] }>('POST', '/api/workspace/saved-searches/check')

export const markNotificationsRead = (id?: number) =>
    request<{ changed: number }>('POST', `/api/workspace/notifications/read${id ? `?notification_id=${id}` : ''}`)

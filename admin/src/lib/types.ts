// Shapes returned by the OSISS admin API (src/admin_api.py).

export type ModelStatus = { key: string; name: string; path: string; present: boolean; enabled: boolean; loaded: boolean }

export type IngesterState = {
  running: boolean
  polling?: boolean
  poll_interval_seconds?: number
  last_scan_at?: number | null
  queued?: number
  current?: Job | null
}

export type AccessState = {
  mode: 'open' | 'key'
  configured: boolean
  cors_origins: string[]
  public_base_url: string
}

export type ServerStatus = {
  version: string
  uptime_seconds: number
  postgres: { ok: boolean; error?: string; books?: number; chunks?: number; pages?: number; excluded?: number; api_keys?: number }
  elasticsearch: { ok: boolean; error?: string; cluster_status?: string; index?: string; index_exists?: boolean; documents?: number }
  models: ModelStatus[]
  gpu: { available: boolean; name?: string; memory_free?: number; memory_total?: number; device?: string; error?: string }
  disk: { path: string; total: number; used: number; free: number }
  ingester: IngesterState
  access: AccessState
  admin_ip_restricted: boolean
  warnings: string[]
}

export type Book = {
  id: number
  title: string
  author: string | null
  publication_year: number | null
  department: string | null
  file_path: string
  language_code: string | null
  file_hash: string | null
  created_at: string | null
  chunk_count: number
  page_count: number
  exercise_chunks: number
  pdf_link: string
  file_exists: boolean
  title_guessed: boolean
}

export type BookMetadata = {
  title: string | null
  author: string | null
  publication_year: number | null
  department: string | null
}

export type DeletedBook = {
  file_path: string
  file_hash: string | null
  metadata: Partial<BookMetadata>
  deleted_at: string | null
  file_exists: boolean
  pdf_link: string
}

export type Job = {
  id: string
  kind: 'upload' | 'reingest' | 'restore' | 'scan' | 'maintenance'
  label: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  message: string
  created_at: number
  started_at: number | null
  finished_at: number | null
  book_id: number | null
}

export type LogEntry = { seq: number; time: number; message: string }

export type SettingField = {
  name: string
  env: string
  kind: 'bool' | 'int' | 'float' | 'choice' | 'origins' | 'url'
  group: 'search' | 'ingestion' | 'load' | 'access'
  label: string
  help: string
  min: number | null
  max: number | null
  choices: string[]
  value: unknown
}

export type SettingsDescription = {
  editable: SettingField[]
  readonly: Array<{ name: string; env: string; value: unknown }>
}

export type Access = {
  search_access: 'open' | 'key'
  configured: boolean
  cors_origins: string[]
  public_base_url: string
}

export type ApiKey = {
  id: number
  name: string
  key_prefix: string
  enabled: boolean
  created_at: string | null
  last_used_at: string | null
  request_count: number
  key?: string
}

export type SearchMetrics = {
  window_seconds: number
  searches: number
  searches_per_minute: number
  cache_hits: number
  cache_hit_rate: number | null
  latency_p50: number | null
  latency_p95: number | null
  stage_seconds: Record<string, number | null>
  refused_rate_limit: number
  refused_busy: number
  running: number
  waiting: number
  concurrency: number
  cache_entries: number
  limits: {
    search_rate_limit_per_minute: number
    api_key_rate_limit_per_minute: number
    search_queue_limit: number
    search_cache_size: number
    search_cache_ttl_seconds: number
    model_precision: string
  }
}

export type ConsoleResult = {
  quote: string
  answer: string
  paragraph: string
  book_title: string | null
  author: string | null
  page_number: number | null
  file_path: string | null
  pdf_link: string
  retrieval_score: number | null
  rerank_score: number | null
  qa_score: number | null
}

export type ConsoleResponse = {
  query: string
  mode: string | null
  variant_queries: Record<string, string>
  timings: Record<string, number>
  results: ConsoleResult[]
}

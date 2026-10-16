import { AlertTriangle, ExternalLink, FileUp, RefreshCw, RotateCcw, Trash2, Upload } from 'lucide-react'
import { useMemo, useState, type ChangeEvent } from 'react'

import type { PickedFile, UploadMetadata } from '../../shared/types.ts'
import { JobStatus } from '../components/JobStatus.tsx'
import { Alert, Badge, Button, Card, Field, Input, Messages, Modal, PageHeader, Spinner } from '../components/ui.tsx'
import { osiss } from '../lib/bridge.ts'
import { formatBytes, timeAgo } from '../lib/format.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { ApiError, useServer } from '../lib/server.ts'
import type { Book, BookMetadata, DeletedBook, Job } from '../lib/types.ts'

type MetadataDraft = { title: string; author: string; publication_year: string; department: string }

function toDraft(metadata: Partial<BookMetadata>): MetadataDraft {
  return {
    title: metadata.title ?? '',
    author: metadata.author ?? '',
    publication_year: metadata.publication_year ? String(metadata.publication_year) : '',
    department: metadata.department ?? '',
  }
}

function MetadataFields({ draft, onChange }: { draft: MetadataDraft; onChange: (draft: MetadataDraft) => void }) {
  const set = (key: keyof MetadataDraft) => (event: ChangeEvent<HTMLInputElement>) => onChange({ ...draft, [key]: event.target.value })
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="md:col-span-2">
        <Field label="Title *">
          <Input value={draft.title} onChange={set('title')} />
        </Field>
      </div>
      <Field label="Author">
        <Input value={draft.author} onChange={set('author')} />
      </Field>
      <Field label="Year">
        <Input value={draft.publication_year} onChange={set('publication_year')} inputMode="numeric" placeholder="2019" />
      </Field>
      <div className="md:col-span-2">
        <Field label="Department">
          <Input value={draft.department} onChange={set('department')} />
        </Field>
      </div>
    </div>
  )
}

function EditBook({ book, onClose, onChanged }: { book: Book; onClose: () => void; onChanged: () => void }) {
  const { call, profile } = useServer()
  const [draft, setDraft] = useState(() => toDraft(book))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  // Re-ingesting creates a new book row; this one is then stale.
  const [replacedBy, setReplacedBy] = useState<number | null>(null)
  const { busy, error, notice, run, setError, setNotice } = useAction()

  const save = () =>
    run(async () => {
      await call({
        method: 'PATCH',
        path: `/api/admin/books/${book.id}`,
        body: { ...draft, publication_year: draft.publication_year.trim() ? Number(draft.publication_year) : null },
      })
      onChanged()
    }, 'Saved. Search results show the new metadata immediately.')

  return (
    <Modal
      wide
      title={book.title}
      onClose={onClose}
      footer={
        <>
          <Button variant="danger" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmDelete(true)} className="mr-auto">
            Delete
          </Button>
          <Button onClick={onClose}>Close</Button>
          <Button variant="primary" busy={busy} disabled={!draft.title.trim() || replacedBy !== null} onClick={save}>
            Save metadata
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {book.title_guessed && (
          <Alert tone="amber">The title and author are still guessed from the file name. Please check them.</Alert>
        )}
        <MetadataFields draft={draft} onChange={setDraft} />
        <div className="rounded-md bg-slate-50 p-3 text-xs text-slate-600">
          <div className="font-mono">{book.file_path}</div>
          <div className="mt-1">
            {book.page_count} pages with text · {book.chunk_count} chunks ({book.exercise_chunks} exercise) · language {book.language_code ?? '—'} · added{' '}
            {timeAgo(book.created_at)}
          </div>
          <div className="mt-2 flex gap-2">
            <Button
              icon={<ExternalLink className="h-4 w-4" />}
              disabled={!book.file_exists}
              onClick={() =>
                run(async () => {
                  const result = await osiss.api.openBookPdf(profile.id, book.id)
                  if (!result.ok) throw new Error(result.error)
                })
              }
            >
              Open PDF
            </Button>
            <Button
              icon={<RefreshCw className="h-4 w-4" />}
              disabled={!book.file_exists || Boolean(jobId)}
              onClick={() =>
                run(async () => {
                  const job = await call<Job>({ method: 'POST', path: `/api/admin/books/${book.id}/reingest` })
                  setJobId(job.id)
                })
              }
            >
              Re-ingest from file
            </Button>
          </div>
        </div>
        {jobId && (
          <JobStatus
            jobId={jobId}
            onFinished={(job) => {
              onChanged()
              if (job.status === 'done') setReplacedBy(job.book_id)
            }}
          />
        )}
        {replacedBy !== null && <Alert tone="green">Re-ingested as book {replacedBy}. Close this window and open it again from the list to edit it.</Alert>}
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      </div>
      {confirmDelete && (
        <Modal
          title="Delete this book from search?"
          onClose={() => setConfirmDelete(false)}
          footer={
            <>
              <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
              <Button
                variant="danger"
                busy={busy}
                onClick={() =>
                  run(async () => {
                    await call({ method: 'DELETE', path: `/api/admin/books/${book.id}` })
                    onChanged()
                    onClose()
                  })
                }
              >
                Delete
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600">
            “{book.title}” is removed from PostgreSQL and Elasticsearch. The PDF file stays on the server and is listed
            under <b>Deleted</b>, where you can restore it with its metadata. The ingester will not add it back on its own.
          </p>
        </Modal>
      )}
    </Modal>
  )
}

function UploadBook({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const { call, profile } = useServer()
  const [file, setFile] = useState<PickedFile | null>(null)
  const [draft, setDraft] = useState<MetadataDraft>(toDraft({}))
  const [jobId, setJobId] = useState<string | null>(null)
  const [finished, setFinished] = useState<Job | null>(null)
  const { busy, error, run, setError } = useAction()

  const pick = () =>
    run(async () => {
      const picked = await osiss.files.pickPdf()
      if (!picked) return
      setFile(picked)
      const guess = await call<BookMetadata>({ path: `/api/admin/metadata-guess?filename=${encodeURIComponent(picked.name)}` })
      setDraft(toDraft(guess))
    })

  const submit = () =>
    run(async () => {
      if (!file) return
      const response = await osiss.api.upload(profile.id, file.path, draft satisfies UploadMetadata)
      if (!response.ok) throw new ApiError(response.error, response.status)
      setJobId((response.data as Job).id)
    })

  return (
    <Modal
      wide
      title="Add a book"
      onClose={onClose}
      footer={
        jobId ? (
          <Button variant="primary" onClick={onClose}>
            {finished ? 'Close' : 'Close (keeps running on the server)'}
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" icon={<Upload className="h-4 w-4" />} busy={busy} disabled={!file || !draft.title.trim()} onClick={submit}>
              Upload and ingest
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Button icon={<FileUp className="h-4 w-4" />} onClick={pick} disabled={Boolean(jobId)}>
            {file ? 'Choose another PDF' : 'Choose PDF…'}
          </Button>
          {file && (
            <span className="truncate text-sm text-slate-600">
              {file.name} · {formatBytes(file.size)}
            </span>
          )}
        </div>
        {file && (
          <>
            <p className="text-xs text-slate-500">Fields are pre-filled from the file name; correct them before uploading.</p>
            <MetadataFields draft={draft} onChange={setDraft} />
          </>
        )}
        {jobId && (
          <JobStatus
            jobId={jobId}
            onFinished={(job) => {
              setFinished(job)
              onChanged()
            }}
          />
        )}
        {error && <Alert onClose={() => setError(null)}>{error}</Alert>}
      </div>
    </Modal>
  )
}

function DeletedList({ onChanged }: { onChanged: () => void }) {
  const { call } = useServer()
  const deleted = useLoader(() => call<{ deleted: DeletedBook[] }>({ path: '/api/admin/books/deleted' }))
  const [jobs, setJobs] = useState<Record<string, string>>({})
  const { error, notice, run, setError, setNotice } = useAction()

  if (!deleted.data) return deleted.error ? <Alert>{deleted.error}</Alert> : <Spinner label="Loading…" />
  const messages = <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
  if (!deleted.data.deleted.length) {
    return (
      <div className="space-y-3">
        {messages}
        <p className="text-sm text-slate-500">No deleted books.</p>
      </div>
    )
  }
  return (
    <div className="space-y-3">
      {messages}
      {deleted.data.deleted.map((item) => (
        <div key={item.file_path} className="rounded-md border border-slate-200 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="font-medium">{item.metadata.title ?? item.file_path}</div>
              <div className="truncate font-mono text-xs text-slate-500">{item.file_path}</div>
              <div className="text-xs text-slate-500">
                Deleted {timeAgo(item.deleted_at)} · {item.file_exists ? 'file kept' : <span className="text-rose-600">file missing</span>}
              </div>
            </div>
            <Button
              icon={<RotateCcw className="h-4 w-4" />}
              disabled={!item.file_exists || Boolean(jobs[item.file_path])}
              onClick={() =>
                run(async () => {
                  const job = await call<Job>({ method: 'POST', path: '/api/admin/books/restore', body: { file_path: item.file_path } })
                  setJobs((current) => ({ ...current, [item.file_path]: job.id }))
                })
              }
            >
              Restore
            </Button>
          </div>
          {jobs[item.file_path] && (
            <div className="mt-2">
              <JobStatus
                jobId={jobs[item.file_path]}
                onFinished={(job) => {
                  if (job.status === 'done') setNotice(`Restored “${item.metadata.title ?? item.file_path}” as book ${job.book_id}.`)
                  else setError(job.message)
                  onChanged()
                  void deleted.reload()
                }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function Books() {
  const { call } = useServer()
  const books = useLoader(() => call<{ books: Book[] }>({ path: '/api/admin/books' }))
  const [filter, setFilter] = useState('')
  const [onlyGuessed, setOnlyGuessed] = useState(false)
  const [tab, setTab] = useState<'books' | 'deleted'>('books')
  const [editing, setEditing] = useState<Book | null>(null)
  const [uploading, setUploading] = useState(false)

  const list = books.data?.books
  const guessedCount = list?.filter((book) => book.title_guessed).length ?? 0
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return (list ?? []).filter(
      (book) =>
        (!onlyGuessed || book.title_guessed) &&
        (!needle || [book.title, book.author, book.department, book.file_path].some((value) => value?.toLowerCase().includes(needle))),
    )
  }, [list, filter, onlyGuessed])

  return (
    <div className="space-y-5">
      <PageHeader
        title="Books"
        description="Fix titles and authors, add new PDFs with their metadata, and remove books from search."
        actions={
          <>
            <Button icon={<RefreshCw className="h-4 w-4" />} onClick={() => books.reload()}>
              Refresh
            </Button>
            <Button variant="primary" icon={<Upload className="h-4 w-4" />} onClick={() => setUploading(true)}>
              Add book
            </Button>
          </>
        }
      />
      <div className="flex gap-1 border-b border-slate-200">
        {(['books', 'deleted'] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === key ? 'border-indigo-600 font-medium text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}
          >
            {key === 'books' ? `Books${list ? ` (${list.length})` : ''}` : 'Deleted'}
          </button>
        ))}
      </div>
      {tab === 'deleted' ? (
        <Card>
          <DeletedList onChanged={() => books.reload()} />
        </Card>
      ) : (
        <Card>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Input className="max-w-sm" placeholder="Filter by title, author, department, file…" value={filter} onChange={(event) => setFilter(event.target.value)} />
            {guessedCount > 0 && (
              <label className="flex items-center gap-2 text-sm text-amber-800">
                <input type="checkbox" checked={onlyGuessed} onChange={(event) => setOnlyGuessed(event.target.checked)} />
                <AlertTriangle className="h-4 w-4" /> Only the {guessedCount} with guessed titles
              </label>
            )}
          </div>
          {books.error && <Alert>{books.error}</Alert>}
          {!list && !books.error && <Spinner label="Loading books…" />}
          {list && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                  <tr>
                    <th className="py-2 pr-3">Title</th>
                    <th className="py-2 pr-3">Author</th>
                    <th className="py-2 pr-3">Year</th>
                    <th className="py-2 pr-3">Department</th>
                    <th className="py-2 pr-3">Lang</th>
                    <th className="py-2 pr-3 text-right">Pages</th>
                    <th className="py-2 text-right">Chunks</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((book) => (
                    <tr key={book.id} className="cursor-pointer border-b border-slate-100 hover:bg-indigo-50/50" onClick={() => setEditing(book)}>
                      <td className="py-2 pr-3">
                        <div className="flex items-center gap-2 font-medium">
                          {book.title}
                          {book.title_guessed && <Badge tone="amber">guessed</Badge>}
                          {!book.file_exists && <Badge tone="red">file missing</Badge>}
                        </div>
                        <div className="max-w-md truncate font-mono text-[11px] text-slate-400">{book.file_path}</div>
                      </td>
                      <td className="py-2 pr-3">{book.author ?? <span className="text-slate-300">—</span>}</td>
                      <td className="py-2 pr-3">{book.publication_year ?? <span className="text-slate-300">—</span>}</td>
                      <td className="py-2 pr-3">{book.department ?? <span className="text-slate-300">—</span>}</td>
                      <td className="py-2 pr-3">{book.language_code}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{book.page_count}</td>
                      <td className="py-2 text-right tabular-nums">{book.chunk_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!visible.length && <p className="py-6 text-center text-sm text-slate-500">{list.length ? 'No books match the filter.' : 'No books yet. Add one or put PDFs in data/pdfs.'}</p>}
            </div>
          )}
        </Card>
      )}
      {editing && (
        <EditBook
          key={editing.id}
          book={editing}
          onClose={() => setEditing(null)}
          onChanged={() => void books.reload()}
        />
      )}
      {uploading && <UploadBook onClose={() => setUploading(false)} onChanged={() => void books.reload()} />}
    </div>
  )
}

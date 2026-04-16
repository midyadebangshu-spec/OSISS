import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Search } from 'lucide-react'
import { ResultCard } from './components/ResultCard'
import { ResultSkeleton } from './components/ResultSkeleton'
import { searchRecords } from './lib/searchApi'
import type { SearchResult } from './types/search'

const EXAMPLE_QUERIES = [
  'What is the boiling point of water?',
  'আলোর প্রতিফলন কাকে বলে?',
  'ऊष्मागतिकी की परिभाषा क्या है?',
]

function App() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<{
    pdfLink: string
    pageNumber: number
    title: string
  } | null>(null)

  const isSearchDisabled = useMemo(() => query.trim().length === 0 || isLoading, [query, isLoading])

  const runSearch = async (searchQuery: string) => {
    const cleaned = searchQuery.trim()
    if (!cleaned) {
      return
    }

    setHasSearched(true)
    setIsLoading(true)
    setError(null)

    try {
      const data = await searchRecords(cleaned)
      setResults(data.results)
      if (data.results.length === 0) {
        setPreview(null)
      }
    } catch (searchError) {
      setResults([])
      setPreview(null)
      setError(searchError instanceof Error ? searchError.message : 'Search request failed.')
    } finally {
      setIsLoading(false)
    }
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await runSearch(query)
  }

  const onExampleClick = async (example: string) => {
    setQuery(example)
    await runSearch(example)
  }

  const onTogglePreview = (result: SearchResult) => {
    const samePage = preview?.pdfLink === result.pdf_link && preview?.pageNumber === result.page_number
    if (samePage) {
      setPreview(null)
      return
    }

    setPreview({
      pdfLink: result.pdf_link,
      pageNumber: result.page_number,
      title: result.book_title,
    })
  }

  const buildPreviewSrc = (value: NonNullable<typeof preview>) => {
    return `${value.pdfLink}#page=${value.pageNumber}`
  }

  return (
    <main className="min-h-screen bg-white text-zinc-950">
      <div className="w-full px-4 py-4 md:px-6 md:py-5">
        {!hasSearched ? (
          <section className="mx-auto flex min-h-[88vh] w-full max-w-6xl flex-col items-center justify-center gap-8">
            <div className="text-center">
              <p className="mb-4 text-xs uppercase tracking-[0.2em] text-zinc-500">Open-Source Institutional Scholar Search</p>
              <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">OSISS Archive Search</h1>
            </div>

            <form onSubmit={onSubmit} className="w-full space-y-3">
              <label htmlFor="search-input" className="sr-only">
                Search textbooks and papers
              </label>
              <div className="flex w-full flex-col gap-3 sm:flex-row">
                <input
                  id="search-input"
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search textbooks and papers in বাংলা, हिंदी, English"
                  className="h-14 w-full rounded-none border-2 border-zinc-950 bg-white px-4 text-base outline-none transition focus:bg-zinc-50"
                />
                <button
                  type="submit"
                  disabled={isSearchDisabled}
                  className="inline-flex h-14 min-w-32 items-center justify-center gap-2 rounded-none border-2 border-zinc-950 bg-zinc-950 px-5 font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-700"
                >
                  <Search className="h-4 w-4" />
                  Search
                </button>
              </div>
            </form>

            <div className="w-full">
              <p className="mb-3 text-sm font-medium text-zinc-700">Example queries</p>
              <div className="flex flex-wrap gap-2">
                {EXAMPLE_QUERIES.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => onExampleClick(item)}
                    className="rounded-none border border-zinc-950 px-3 py-2 text-left text-sm transition hover:bg-zinc-100"
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          </section>
        ) : (
          <section className="space-y-4">
            <header className="sticky top-0 z-10 border-b-2 border-zinc-950 bg-white pb-4 pt-1">
              <p className="mb-2 text-xs uppercase tracking-[0.2em] text-zinc-500">OSISS Archive Search</p>
              <form onSubmit={onSubmit} className="flex w-full flex-col gap-3 sm:flex-row">
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search textbooks and papers"
                  className="h-12 w-full rounded-none border-2 border-zinc-950 bg-white px-4 text-base outline-none focus:bg-zinc-50"
                />
                <button
                  type="submit"
                  disabled={isSearchDisabled}
                  className="inline-flex h-12 min-w-28 items-center justify-center gap-2 rounded-none border-2 border-zinc-950 bg-zinc-950 px-4 font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-700"
                >
                  <Search className="h-4 w-4" />
                  Search
                </button>
              </form>
            </header>

            <div className={`grid gap-4 ${preview ? 'xl:grid-cols-[minmax(0,1fr)_36vw]' : 'grid-cols-1'}`}>
              <div className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">Database Hits</h2>

                {isLoading && (
                  <div className="space-y-3">
                    <ResultSkeleton />
                    <ResultSkeleton />
                    <ResultSkeleton />
                  </div>
                )}

                {!isLoading && error && (
                  <div className="border-2 border-zinc-950 bg-white p-4 text-sm text-zinc-800">{error}</div>
                )}

                {!isLoading && !error && results.length === 0 && (
                  <div className="border-2 border-zinc-950 bg-white p-4 text-sm text-zinc-800">No matching records found for this query.</div>
                )}

                {!isLoading &&
                  !error &&
                  results.map((result, index) => (
                    <ResultCard
                      key={`${result.book_title}-${index}`}
                      result={result}
                      onViewPage={onTogglePreview}
                      isPreviewOpen={preview?.pdfLink === result.pdf_link && preview?.pageNumber === result.page_number}
                    />
                  ))}
              </div>

              {preview && (
                <aside className="sticky top-20 h-[calc(100vh-6rem)] border-2 border-zinc-950 bg-white">
                  <div className="border-b border-zinc-950 px-3 py-2">
                    <p className="text-xs uppercase tracking-[0.15em] text-zinc-500">Page Viewer</p>
                    <p className="truncate text-sm font-medium">{preview.title} · Page {preview.pageNumber}</p>
                  </div>
                  <iframe
                    title="Document page preview"
                    src={buildPreviewSrc(preview)}
                    className="h-[calc(100%-56px)] w-full border-0"
                  />
                </aside>
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  )
}

export default App

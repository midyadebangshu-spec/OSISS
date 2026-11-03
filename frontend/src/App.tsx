import { useCallback, useMemo, useState } from 'react'
import { Network, Search, X } from 'lucide-react'
import { Lightbox } from './components/Lightbox'
import { ExplorerPage } from './pages/ExplorerPage'
import { SearchPage } from './pages/SearchPage'
import { useRoute } from './lib/route'
import { ViewerContext } from './lib/viewer'
import type { LightboxItem, Viewer } from './lib/viewer'
import type { PagePreview } from './types/search'

function NavLink({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      className={`inline-flex items-center gap-1.5 border-b-2 px-1 py-2 text-sm font-medium ${
        active ? 'border-zinc-950 text-zinc-950' : 'border-transparent text-zinc-500 hover:text-zinc-950'
      }`}
    >
      {children}
    </a>
  )
}

function App() {
  const route = useRoute()
  const [lightbox, setLightbox] = useState<{ items: LightboxItem[]; index: number } | null>(null)
  const [preview, setPreview] = useState<PagePreview | null>(null)

  const togglePage = useCallback((next: PagePreview) => {
    setPreview((current) =>
      current?.pdfLink === next.pdfLink && current?.pageNumber === next.pageNumber ? null : next,
    )
  }, [])

  const viewer = useMemo<Viewer>(
    () => ({
      openLightbox: (items, index) => setLightbox({ items, index }),
      togglePage,
      isPageOpen: (pdfLink, pageNumber) => preview?.pdfLink === pdfLink && preview?.pageNumber === pageNumber,
    }),
    [preview, togglePage],
  )

  const closeLightbox = useCallback(() => setLightbox(null), [])
  const setLightboxIndex = useCallback(
    (index: number) => setLightbox((current) => (current ? { ...current, index } : current)),
    [],
  )

  return (
    <ViewerContext.Provider value={viewer}>
      <main className="min-h-screen bg-white text-zinc-950">
        <nav className="flex items-center justify-between gap-4 border-b border-zinc-200 px-4 md:px-6" aria-label="Main">
          <a href="#/" className="py-2 text-xs font-semibold uppercase tracking-[0.2em] text-zinc-700">
            OSISS
          </a>
          <div className="flex items-center gap-5">
            <NavLink href="#/" active={route.page === 'search'}>
              <Search className="h-4 w-4" />
              Search
            </NavLink>
            <NavLink href="#/explore" active={route.page === 'explore'}>
              <Network className="h-4 w-4" />
              Explore
            </NavLink>
          </div>
        </nav>

        <div className={`w-full px-4 py-4 md:px-6 md:py-5 ${preview ? 'xl:grid xl:grid-cols-[minmax(0,1fr)_36vw] xl:gap-4' : ''}`}>
          <div className="min-w-0">
            {/* Both pages stay mounted so switching tabs keeps the search results and the open concept. */}
            <div hidden={route.page !== 'search'}>
              <SearchPage />
            </div>
            <div hidden={route.page !== 'explore'}>
              <ExplorerPage conceptId={route.page === 'explore' ? route.conceptId : null} active={route.page === 'explore'} />
            </div>
          </div>

          {preview && (
            <aside className="mt-4 h-[80vh] border-2 border-zinc-950 bg-white xl:sticky xl:top-4 xl:mt-0 xl:h-[calc(100vh-5rem)]">
              <div className="flex items-start justify-between gap-2 border-b border-zinc-950 px-3 py-2">
                <div className="min-w-0">
                  <p className="text-xs uppercase tracking-[0.15em] text-zinc-500">PDF Viewer</p>
                  <p className="truncate text-sm font-medium">
                    {preview.title} · Page {preview.pageNumber}
                  </p>
                </div>
                <button type="button" onClick={() => setPreview(null)} className="shrink-0 p-1 hover:bg-zinc-100" aria-label="Close PDF viewer">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <iframe
                title="Document page preview"
                src={`${preview.pdfLink}#page=${preview.pageNumber}`}
                className="h-[calc(100%-56px)] w-full border-0"
              />
            </aside>
          )}
        </div>

        {lightbox && <Lightbox items={lightbox.items} index={lightbox.index} onIndex={setLightboxIndex} onClose={closeLightbox} />}
      </main>
    </ViewerContext.Provider>
  )
}

export default App

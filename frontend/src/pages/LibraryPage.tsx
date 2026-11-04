import { useEffect, useMemo, useState } from 'react'
import { Library } from 'lucide-react'
import { BookCover } from '../components/library/BookCover'
import { BookPage } from './BookPage'
import { ReaderPage } from './ReaderPage'
import { LANGUAGE_LABEL, listBooks } from '../lib/libraryApi'
import { libraryHref } from '../lib/route'
import type { LibraryBook } from '../types/library'

type SortKey = 'title' | 'pages' | 'added'

function BookCard({ book }: { book: LibraryBook }) {
    return (
        <a href={libraryHref(book.id)} className="group flex flex-col border-2 border-zinc-950 bg-white hover:-translate-y-0.5 hover:shadow-[4px_4px_0_0_#18181b]">
            <BookCover book={book} className="aspect-[3/4] w-full border-b-2 border-zinc-950" />
            <div className="flex flex-1 flex-col gap-1 p-3">
                <p className="line-clamp-2 font-semibold leading-snug group-hover:underline">{book.title}</p>
                {book.author && <p className="truncate text-sm text-zinc-600">{book.author}</p>}
                <p className="mt-auto flex flex-wrap gap-x-2 pt-1 text-xs text-zinc-500">
                    {book.language && <span className="border border-zinc-300 px-1.5">{LANGUAGE_LABEL[book.language] ?? book.language}</span>}
                    {book.page_count !== null && <span>{book.page_count} pages</span>}
                </p>
            </div>
        </a>
    )
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) {
    return (
        <label className="flex items-center gap-2 text-sm">
            <span className="text-zinc-500">{label}</span>
            <select value={value} onChange={(event) => onChange(event.target.value)} className="h-10 border-2 border-zinc-950 bg-white px-2">
                {options.map(([key, text]) => (
                    <option key={key} value={key}>
                        {text}
                    </option>
                ))}
            </select>
        </label>
    )
}

function Shelf({ active }: { active: boolean }) {
    const [books, setBooks] = useState<LibraryBook[] | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [text, setText] = useState('')
    const [language, setLanguage] = useState('all')
    const [subject, setSubject] = useState('all')
    const [sort, setSort] = useState<SortKey>('title')

    useEffect(() => {
        if (!active || books !== null) return
        const controller = new AbortController()
        listBooks(controller.signal)
            .then(setBooks)
            .catch((reason: unknown) => {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load the library.')
            })
        return () => controller.abort()
    }, [active, books])

    const subjects = useMemo(() => [...new Set((books ?? []).map((book) => book.department || 'Other'))].sort(), [books])
    const languages = useMemo(() => [...new Set((books ?? []).map((book) => book.language).filter(Boolean))] as string[], [books])

    const shelves = useMemo(() => {
        const needle = text.trim().toLowerCase()
        const shown = (books ?? []).filter(
            (book) =>
                (language === 'all' || book.language === language) &&
                (subject === 'all' || (book.department || 'Other') === subject) &&
                (!needle || `${book.title} ${book.author ?? ''}`.toLowerCase().includes(needle)),
        )
        const compare = (a: LibraryBook, b: LibraryBook) =>
            sort === 'pages'
                ? (b.page_count ?? 0) - (a.page_count ?? 0)
                : sort === 'added'
                  ? (b.added ?? '').localeCompare(a.added ?? '')
                  : a.title.localeCompare(b.title)
        const grouped = new Map<string, LibraryBook[]>()
        for (const book of shown) {
            const key = book.department || 'Other'
            grouped.set(key, [...(grouped.get(key) ?? []), book])
        }
        return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, items]) => [name, items.sort(compare)] as const)
    }, [books, text, language, subject, sort])

    const total = shelves.reduce((sum, [, items]) => sum + items.length, 0)
    const pages = (books ?? []).reduce((sum, book) => sum + (book.page_count ?? 0), 0)

    return (
        <div className="space-y-6">
            <div className="space-y-2">
                <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight md:text-3xl">
                    <Library className="h-7 w-7" />
                    Library
                </h1>
                {books && (
                    <p className="text-zinc-600">
                        {books.length} books · {pages.toLocaleString()} pages in English, বাংলা and हिंदी
                    </p>
                )}
            </div>

            <div className="sticky top-0 z-10 flex flex-col gap-3 border-b-2 border-zinc-950 bg-white pb-4 pt-2 lg:flex-row lg:items-center">
                <input
                    type="search"
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="Filter by title or author"
                    aria-label="Filter by title or author"
                    className="h-10 w-full border-2 border-zinc-950 px-3 outline-none focus:bg-zinc-50 lg:max-w-sm"
                />
                <div className="flex flex-wrap gap-3">
                    <Select
                        label="Language"
                        value={language}
                        onChange={setLanguage}
                        options={[['all', 'All'], ...languages.map((code) => [code, LANGUAGE_LABEL[code] ?? code] as [string, string])]}
                    />
                    <Select label="Subject" value={subject} onChange={setSubject} options={[['all', 'All'], ...subjects.map((name) => [name, name] as [string, string])]} />
                    <Select
                        label="Sort"
                        value={sort}
                        onChange={(value) => setSort(value as SortKey)}
                        options={[
                            ['title', 'Title'],
                            ['pages', 'Most pages'],
                            ['added', 'Recently added'],
                        ]}
                    />
                </div>
                {books && <p className="text-sm text-zinc-500 lg:ml-auto">{total} shown</p>}
            </div>

            {error && <p className="border-2 border-zinc-950 p-4 text-sm">{error}</p>}
            {!books && !error && (
                <div className="grid animate-pulse grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
                    {Array.from({ length: 10 }, (_, index) => (
                        <div key={index} className="aspect-[3/4] bg-zinc-100" />
                    ))}
                </div>
            )}
            {books && total === 0 && <p className="border-2 border-zinc-950 p-4 text-sm">No book matches these filters.</p>}

            {shelves.map(([name, items]) => (
                <section key={name} className="space-y-3">
                    <div className="flex items-baseline justify-between border-b border-zinc-300 pb-1">
                        <h2 className="text-lg font-semibold tracking-tight">{name}</h2>
                        <p className="text-xs uppercase tracking-[0.16em] text-zinc-500">
                            {items.length} book{items.length === 1 ? '' : 's'}
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-6">
                        {items.map((book) => (
                            <BookCard key={book.id} book={book} />
                        ))}
                    </div>
                </section>
            ))}
        </div>
    )
}

export function LibraryPage({ bookId, readPage, active }: { bookId: number | null; readPage: number | null; active: boolean }) {
    return (
        <>
            <div hidden={bookId !== null}>
                <Shelf active={active} />
            </div>
            {bookId !== null && readPage === null && <BookPage key={bookId} bookId={bookId} />}
            {bookId !== null && readPage !== null && <ReaderPage key={`${bookId}-${readPage}`} bookId={bookId} page={readPage} />}
        </>
    )
}


import { BookOpen } from 'lucide-react'
import type { LibraryBook } from '../../types/library'

/** A book's first page as its cover, or a placeholder when no page image exists yet. */
export function BookCover({ book, className = '' }: { book: Pick<LibraryBook, 'cover'>; className?: string }) {
    if (!book.cover) {
        return (
            <div className={`flex items-center justify-center bg-zinc-100 text-zinc-400 ${className}`}>
                <BookOpen className="h-8 w-8" />
            </div>
        )
    }
    return <img src={book.cover} alt="" loading="lazy" className={`bg-zinc-100 object-cover object-top ${className}`} />
}

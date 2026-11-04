import { createContext, useContext } from 'react'

/** The question the shown results answer (flashcard prompts use it). */
export const SearchQueryContext = createContext('')

export function useSearchQuery(): string {
    return useContext(SearchQueryContext)
}

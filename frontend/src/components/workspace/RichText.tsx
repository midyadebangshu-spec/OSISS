import { italicRuns } from '../../lib/richText'

/** Text with `*italic*` markers shown with real italics. */
export function RichText({ text }: { text: string }) {
    return (
        <>
            {italicRuns(text).map((run, index) => (run.italic ? <em key={index}>{run.text}</em> : <span key={index}>{run.text}</span>))}
        </>
    )
}

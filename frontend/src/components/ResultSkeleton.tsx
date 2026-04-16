export function ResultSkeleton() {
    return (
        <div className="animate-pulse border-2 border-zinc-300 bg-white p-5">
            <div className="mb-4 h-3 w-28 bg-zinc-200" />
            <div className="space-y-2 border-l-4 border-zinc-300 bg-zinc-100 p-3">
                <div className="h-3 w-full bg-zinc-200" />
                <div className="h-3 w-11/12 bg-zinc-200" />
                <div className="h-3 w-3/4 bg-zinc-200" />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
                <div className="h-3 w-2/3 bg-zinc-200" />
                <div className="h-3 w-1/2 bg-zinc-200" />
                <div className="h-3 w-2/3 bg-zinc-200" />
                <div className="h-3 w-1/3 bg-zinc-200" />
            </div>
        </div>
    )
}

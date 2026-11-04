import { BookMarked, Layers, FolderKanban, FolderOpen, LayoutDashboard, NotebookPen, PenLine } from 'lucide-react'
import { useAuth } from '../lib/auth'
import { workspaceHref } from '../lib/route'
import type { WorkspaceTab } from '../lib/route'
import { CollectionsView } from '../components/workspace/CollectionsView'
import { DashboardView } from '../components/workspace/DashboardView'
import { NotesView } from '../components/workspace/NotesView'
import { ProjectsView } from '../components/workspace/ProjectsView'
import { ReferencesView } from '../components/workspace/ReferencesView'
import { BoardView } from '../components/workspace/BoardView'
import { StudyView } from '../components/workspace/StudyView'
import { WritingView } from '../components/workspace/WritingView'

const TABS: { tab: WorkspaceTab; label: string; icon: React.ReactNode }[] = [
    { tab: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard className="h-4 w-4" /> },
    { tab: 'projects', label: 'Projects', icon: <FolderKanban className="h-4 w-4" /> },
    { tab: 'collections', label: 'Collections', icon: <FolderOpen className="h-4 w-4" /> },
    { tab: 'notes', label: 'Notes', icon: <NotebookPen className="h-4 w-4" /> },
    { tab: 'writing', label: 'Writing', icon: <PenLine className="h-4 w-4" /> },
    { tab: 'study', label: 'Study', icon: <Layers className="h-4 w-4" /> },
    { tab: 'references', label: 'References', icon: <BookMarked className="h-4 w-4" /> },
]

/** The signed-in scholar's workspace. Only mounted while its route is open, so each visit loads fresh data. */
export function WorkspacePage({ tab, id }: { tab: WorkspaceTab; id: number | null }) {
    const { user } = useAuth()

    if (user === undefined) return <p className="py-8 text-sm text-zinc-500">Checking your session…</p>
    if (user === null || user.must_change_password) {
        return (
            <section className="mx-auto max-w-md space-y-3 py-10 text-center">
                <h1 className="text-xl font-semibold">Your workspace</h1>
                <p className="text-sm text-zinc-600">{user ? 'Choose a new password to continue.' : 'Sign in to save passages, build collections and take notes.'}</p>
                <a href="#/account" className="inline-block border-2 border-zinc-950 bg-zinc-950 px-4 py-2 text-sm font-medium text-white">{user ? 'Choose a password' : 'Sign in or create an account'}</a>
            </section>
        )
    }

    return (
        <section className="mx-auto max-w-6xl space-y-5">
            <nav aria-label="Workspace" className="flex flex-wrap gap-x-5 border-b border-zinc-200">
                {TABS.map((item) => (
                    <a
                        key={item.tab}
                        href={workspaceHref(item.tab)}
                        aria-current={item.tab === tab ? 'page' : undefined}
                        className={`inline-flex items-center gap-1.5 border-b-2 px-1 py-2 text-sm font-medium ${item.tab === tab ? 'border-zinc-950 text-zinc-950' : 'border-transparent text-zinc-500 hover:text-zinc-950'}`}
                    >
                        {item.icon}{item.label}
                    </a>
                ))}
            </nav>
            {tab === 'dashboard' && <DashboardView />}
            {tab === 'projects' && <ProjectsView id={id} />}
            {tab === 'collections' && <CollectionsView id={id} />}
            {tab === 'notes' && <NotesView id={id} />}
            {tab === 'board' && id !== null && <BoardView key={id} projectId={id} />}
            {tab === 'writing' && <WritingView id={id} />}
            {tab === 'study' && <StudyView />}
            {tab === 'references' && <ReferencesView />}
        </section>
    )
}

import { Globe, KeyRound } from 'lucide-react'

export type AccessMode = 'open' | 'key'

const OPTIONS: Array<{ mode: AccessMode; title: string; icon: typeof Globe; points: string[] }> = [
  {
    mode: 'open',
    title: 'Open — everyone who can reach the server',
    icon: Globe,
    points: [
      'Anyone on the network (or internet, if the server is public) can search and open the PDFs.',
      'Apps may still send an API key so you can see their usage, but no key is required.',
      'Best for a campus LAN or offline setup where every user is trusted.',
    ],
  },
  {
    mode: 'key',
    title: 'Approved apps only — API key required',
    icon: KeyRound,
    points: [
      'Apps must send a key you create here; you can see their usage and revoke a key in one click.',
      'Your own website (this server, or a domain in Allowed origins) keeps working without a key.',
      'Light lock for the website: browsers keep other sites out, but a script can fake the website’s origin. Fully private search needs user logins, which OSISS does not have yet.',
    ],
  },
]

export function AccessModeChoice({ value, onChange }: { value: AccessMode | null; onChange: (mode: AccessMode) => void }) {
  return (
    <div className="grid gap-3 md:grid-cols-2" role="radiogroup" aria-label="Search access">
      {OPTIONS.map(({ mode, title, icon: Icon, points }) => (
        <button
          key={mode}
          type="button"
          role="radio"
          aria-checked={value === mode}
          onClick={() => onChange(mode)}
          className={`rounded-lg border p-4 text-left transition ${
            value === mode ? 'border-black bg-neutral-100 ring-2 ring-neutral-300' : 'border-slate-200 bg-white hover:border-slate-300'
          }`}
        >
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Icon className="h-4 w-4 text-neutral-900" />
            {title}
          </div>
          <ul className="list-disc space-y-1 pl-5 text-xs text-slate-600">
            {points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </button>
      ))}
    </div>
  )
}

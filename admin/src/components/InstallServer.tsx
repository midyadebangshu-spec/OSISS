import { Download, ExternalLink, FolderOpen, Play, RefreshCw, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { INSTALL_SCRIPT_SOURCE_URL, serverInstallCommand, serverInstallFileCommand, validateInstallDir } from '../../shared/install.ts'
import { osiss } from '../lib/bridge.ts'
import { useAction, useLoader } from '../lib/hooks.ts'
import { Alert, Badge, Button, Card, Field, Input, Messages, Spinner } from './ui.tsx'

function CommandBox({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-start gap-2 rounded-md bg-slate-900 p-3">
      <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-xs leading-5 text-slate-100">{command}</code>
      <Button
        onClick={async () => {
          await osiss.copyText(command)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

/**
 * Install a server on this computer by running the project's installer. Shown while there is no local server and
 * while an install is running or has just finished.
 */
export function InstallServer({
  hasLocalServer,
  onFinished,
  onOpen,
}: {
  hasLocalServer: boolean
  /** Called once when an install has finished and the server was added (refresh the server list). */
  onFinished: (profileId: string) => void
  onOpen: (profileId: string) => void
}) {
  // The status call is tiny while nothing runs (an empty log), so a fixed interval is fine.
  const status = useLoader(() => osiss.install.status(), 2000)
  const [typedDir, setTypedDir] = useState<string | null>(null)
  const { busy, error, run, setError } = useAction()
  const logBox = useRef<HTMLDivElement>(null)
  const announced = useRef<string | null>(null)

  const data = status.data
  const running = data?.state === 'running'
  const dir = typedDir ?? data?.defaultDir ?? ''

  useEffect(() => {
    if (data?.state === 'done' && data.profileId && announced.current !== data.profileId) {
      announced.current = data.profileId
      onFinished(data.profileId)
    }
  }, [data, onFinished])

  useEffect(() => {
    const element = logBox.current
    if (element) element.scrollTop = element.scrollHeight
  }, [data?.log])

  if (!data) return null

  if (!data.supported) {
    return hasLocalServer ? null : (
      <Card title="Install a server">
        <p className="text-sm text-slate-600">{data.reason}</p>
        <p className="mt-2 text-sm text-slate-600">
          To use a server that runs on another computer, choose <b>Add server</b> and pick <b>Elsewhere</b>; the form there has the installer for that computer.
        </p>
      </Card>
    )
  }

  const idle = data.state === 'idle'
  if (idle && hasLocalServer) return null

  const dirProblem = dir ? validateInstallDir(dir) : null
  const start = async () => {
    setError(null)
    const result = await run(() => osiss.install.start(dir))
    if (result && !result.ok) setError(result.error ?? 'Could not start the installer.')
    else void status.reload()
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Install a server on this computer
          {data.state === 'running' && <Badge tone="amber">Running</Badge>}
          {data.state === 'done' && <Badge tone="green">Installed</Badge>}
          {data.state === 'failed' && <Badge tone="red">Stopped</Badge>}
          {data.state === 'blocked' && <Badge tone="amber">Needs you</Badge>}
        </span>
      }
    >
      <div className="space-y-4">
        {(idle || data.state === 'failed' || data.state === 'blocked') && (
          <>
            <p className="text-sm text-slate-600">
              Use this when the server and this app are on the same computer. It runs the OSISS installer, which installs what is missing (Python, Docker, Tesseract and Node.js), downloads the OSISS code and
              the search models (about 12&nbsp;GB), sets up the database and creates the admin token. Then this app adds the server for you; you never copy the token.
            </p>
            <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
              <li>You may be asked for your administrator password once, by the system and not by this app.</li>
              <li>It needs an internet connection, about 40&nbsp;GB of free disk and 16&nbsp;GB of memory, and takes 10 to 30 minutes.</li>
              <li>Running it again is safe: your books, settings and token are kept.</li>
            </ul>
            <Field label="Install folder" hint="Use a folder you own, for example in your home folder.">
              <div className="flex gap-2">
                <Input value={dir} onChange={(event) => setTypedDir(event.target.value)} />
                <Button
                  icon={<FolderOpen className="h-4 w-4" />}
                  onClick={async () => {
                    const picked = await osiss.files.pickDirectory()
                    if (picked) setTypedDir(`${picked.replace(/\/+$/, '')}/osiss`)
                  }}
                >
                  Browse
                </Button>
              </div>
            </Field>
            {dirProblem && <Alert tone="amber">{dirProblem}</Alert>}
          </>
        )}

        {data.state === 'blocked' && (
          <Alert tone="amber">
            <p>{data.message}</p>
            {data.missing.length > 0 && (
              <ul className="mt-1 list-disc pl-5">
                {data.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
            {data.manualCommand && (
              <div className="mt-2 space-y-2">
                <p>In a terminal on this computer:</p>
                <CommandBox command={data.manualCommand} />
              </div>
            )}
          </Alert>
        )}

        {data.state === 'failed' && data.message && <Alert>{data.message}</Alert>}

        {(running || data.log) && (
          <div className="space-y-2">
            {running && <Spinner label={`${data.step}…  Keep this app open; this can take a while.`} />}
            <div ref={logBox} className="h-64 overflow-y-auto whitespace-pre-wrap rounded bg-slate-900 p-3 font-mono text-xs leading-5 text-slate-100" data-testid="install-log">
              {data.log || <span className="text-slate-500">Waiting for output…</span>}
            </div>
          </div>
        )}

        {data.state === 'done' && (
          <Alert tone="green">
            <p>
              The server is installed in <code>{data.dir}</code>
              {data.profileId ? ' and was added here as “This computer”.' : '.'}
            </p>
            {data.profileId ? (
              <p className="mt-1">Next: open the Dashboard, press <b>Start</b> for the database and <b>Start API</b>, then add your PDFs.</p>
            ) : (
              <p className="mt-1">Add it under <b>Add server</b> (On this computer), pick the folder above, and press <b>Read token from .env</b>.</p>
            )}
          </Alert>
        )}

        <Messages error={error ?? status.error} onClear={() => setError(null)} />

        <div className="flex flex-wrap items-center gap-2">
          {(idle || data.state === 'failed' || data.state === 'blocked') && (
            <Button variant="primary" busy={busy} disabled={!dir || Boolean(dirProblem)} icon={data.state === 'blocked' ? <RefreshCw className="h-4 w-4" /> : <Play className="h-4 w-4" />} onClick={start}>
              {data.state === 'blocked' ? 'Check again' : data.state === 'failed' ? 'Try again' : 'Install the server'}
            </Button>
          )}
          {running && (
            <Button icon={<Square className="h-4 w-4" />} disabled={!data.canCancel} onClick={() => osiss.install.cancel().then(() => status.reload())}>
              {data.canCancel ? 'Cancel' : 'Waiting for permission…'}
            </Button>
          )}
          {data.state === 'done' && data.profileId && (
            <Button variant="primary" onClick={() => onOpen(data.profileId!)}>
              Open the Dashboard
            </Button>
          )}
          <Button variant="ghost" icon={<ExternalLink className="h-4 w-4" />} onClick={() => void osiss.openExternal(INSTALL_SCRIPT_SOURCE_URL)}>
            View the installer script
          </Button>
        </div>
      </div>
    </Card>
  )
}

/** For a server on another computer: the command to run there, or the script to copy over. */
export function ServerScriptHelp() {
  const [listenAll, setListenAll] = useState(true)
  const [service, setService] = useState(true)
  const [noWebsite, setNoWebsite] = useState(false)
  const { run, error, notice, setError, setNotice } = useAction()
  const options = { listenAll, service, noWebsite }

  return (
    <details className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 md:col-span-2">
      <summary className="cursor-pointer text-sm font-medium text-slate-800">The server is not installed yet? Install it on that computer</summary>
      <div className="mt-3 space-y-3 text-sm text-slate-600">
        <p>
          Run the installer on the Ubuntu, Debian or macOS machine that will host OSISS. It sets up everything (Python, Docker, the database and the models) and prints the server address and the admin token at the end.
          Then come back here, enter the address as the <b>Server URL</b> and paste the token.
        </p>
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={listenAll} onChange={(event) => setListenAll(event.target.checked)} />
            Let other computers reach it (needed for this app)
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={service} onChange={(event) => setService(event.target.checked)} />
            Start it at boot (Linux service)
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={noWebsite} onChange={(event) => setNoWebsite(event.target.checked)} />
            No website on this server
          </label>
        </div>
        <p className="font-medium text-slate-700">One command, in a terminal on the server:</p>
        <CommandBox command={serverInstallCommand(options)} />
        <p className="font-medium text-slate-700">Or save the script, copy it to the server, read it, and run it:</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            icon={<Download className="h-4 w-4" />}
            onClick={() =>
              run(async () => {
                const result = await osiss.install.saveScript()
                if (result.error) throw new Error(result.error)
                if (result.ok) setNotice(`Saved to ${result.path}.`)
              })
            }
          >
            Save install.sh…
          </Button>
          <code className="rounded bg-white px-2 py-1 font-mono text-xs ring-1 ring-slate-200">{serverInstallFileCommand(options)}</code>
        </div>
        <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
        <p>The token stays on the server in <code>.env</code> inside the install folder. Treat it like a password.</p>
      </div>
    </details>
  )
}

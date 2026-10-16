import { useState } from 'react'

import { callApi, errorText } from '../lib/server.ts'
import { AccessModeChoice, type AccessMode } from './AccessModeChoice.tsx'
import { Alert, Button, Field, Input, Modal } from './ui.tsx'

/** Shown the first time a server is connected whose search access mode has not been chosen yet. */
export function AccessWizard({
  profileId,
  serverName,
  onDone,
  onLater,
}: {
  profileId: string
  serverName: string
  onDone: () => void
  onLater: () => void
}) {
  const [mode, setMode] = useState<AccessMode | null>(null)
  const [origins, setOrigins] = useState('')
  const [publicUrl, setPublicUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (!mode) return
    setBusy(true)
    setError(null)
    try {
      await callApi(profileId, {
        method: 'PUT',
        path: '/api/admin/access',
        body: {
          search_access: mode,
          cors_origins: origins.split(/[\s,]+/).filter(Boolean),
          public_base_url: publicUrl.trim(),
        },
      })
      onDone()
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      wide
      title={`How should “${serverName}” serve search?`}
      footer={
        <>
          <Button variant="ghost" onClick={onLater}>
            Decide later
          </Button>
          <Button variant="primary" busy={busy} disabled={!mode} onClick={save}>
            Save choice
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-slate-600">
        Choose who may call the search API and open PDFs. Until you choose, the server stays open as before. You can
        change this any time under <b>Access &amp; API keys</b>.
      </p>
      <AccessModeChoice value={mode} onChange={setMode} />
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <Field label="Website domains (optional)" hint="Browser origins allowed to call the API, e.g. https://search.college.edu">
          <Input value={origins} onChange={(event) => setOrigins(event.target.value)} placeholder="https://search.college.edu" />
        </Field>
        <Field label="Public URL of this server (optional)" hint="Makes PDF links in API results absolute, for apps.">
          <Input value={publicUrl} onChange={(event) => setPublicUrl(event.target.value)} placeholder="https://search.college.edu" />
        </Field>
      </div>
      {error && (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      )}
    </Modal>
  )
}

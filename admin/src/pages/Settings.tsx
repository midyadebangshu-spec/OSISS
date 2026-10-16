import { RotateCcw, Save } from 'lucide-react'
import { useState } from 'react'

import { Alert, Button, Card, Input, Messages, PageHeader, Spinner, Toggle } from '../components/ui.tsx'
import { useAction, useLoader } from '../lib/hooks.ts'
import { useServer } from '../lib/server.ts'
import type { SettingField, SettingsDescription } from '../lib/types.ts'

function FieldInput({ field, value, onChange }: { field: SettingField; value: unknown; onChange: (value: unknown) => void }) {
  if (field.kind === 'bool') return <Toggle label={field.label} checked={Boolean(value)} onChange={onChange} />
  if (field.kind === 'choice') {
    return (
      <select className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm" value={String(value)} onChange={(event) => onChange(event.target.value)} aria-label={field.label}>
        {field.choices.map((choice) => (
          <option key={choice}>{choice}</option>
        ))}
      </select>
    )
  }
  return (
    <Input
      className="w-32 text-right tabular-nums"
      type="number"
      step={field.kind === 'int' ? 1 : 0.05}
      min={field.min ?? undefined}
      max={field.max ?? undefined}
      value={value === '' ? '' : String(value)}
      onChange={(event) => onChange(event.target.value === '' ? '' : Number(event.target.value))}
      aria-label={field.label}
    />
  )
}

export function SettingsPage() {
  const { call, profile } = useServer()
  const settings = useLoader(() => call<SettingsDescription>({ path: '/api/admin/settings' }), undefined, [profile.id])
  const [changes, setChanges] = useState<Record<string, unknown>>({})
  const { busy, error, notice, run, setError, setNotice } = useAction()

  if (!settings.data) return settings.error ? <Alert>{settings.error}</Alert> : <Spinner label="Loading settings…" />
  const fields = settings.data.editable.filter((field) => field.group !== 'access')
  const groups = [
    { key: 'search', title: 'Search pipeline' },
    { key: 'ingestion', title: 'Ingestion' },
  ]
  const dirty = Object.keys(changes).length > 0

  const save = () =>
    run(async () => {
      await call({ method: 'PUT', path: '/api/admin/settings', body: changes })
      setChanges({})
      await settings.reload()
    }, 'Saved. New searches use these values; they are kept across restarts.')

  return (
    <div className="space-y-5">
      <PageHeader
        title="Search settings"
        description="Values the server reads on every request, changed without a restart. Try them in the Search console. Saved values override the server's environment."
        actions={
          <>
            <Button icon={<RotateCcw className="h-4 w-4" />} disabled={!dirty} onClick={() => setChanges({})}>
              Discard
            </Button>
            <Button variant="primary" icon={<Save className="h-4 w-4" />} busy={busy} disabled={!dirty} onClick={save}>
              Save changes
            </Button>
          </>
        }
      />
      <Messages error={error} notice={notice} onClear={() => (setError(null), setNotice(null))} />
      {groups.map((group) => (
        <Card key={group.key} title={group.title}>
          <div className="divide-y divide-slate-100">
            {fields
              .filter((field) => field.group === group.key)
              .map((field) => {
                const value = field.name in changes ? changes[field.name] : field.value
                return (
                  <div key={field.name} className="flex items-center justify-between gap-4 py-2.5">
                    <div className="min-w-0">
                      <div className="text-sm font-medium">
                        {field.label}
                        {field.name in changes && <span className="ml-2 text-xs text-neutral-900">changed</span>}
                      </div>
                      <div className="text-xs text-slate-500">
                        <code>{field.env}</code>
                        {field.help && ` · ${field.help}`}
                        {field.min !== null && field.max !== null && ` (${field.min}–${field.max})`}
                      </div>
                    </div>
                    <FieldInput
                      field={field}
                      value={value}
                      onChange={(next) =>
                        setChanges((current) => {
                          const updated = { ...current, [field.name]: next }
                          if (next === field.value) delete updated[field.name]
                          return updated
                        })
                      }
                    />
                  </div>
                )
              })}
          </div>
        </Card>
      ))}
      <Card title="Needs a restart (read-only)">
        <p className="mb-3 text-xs text-slate-500">Change these in the server's environment or .env file and restart the API.</p>
        <dl className="grid gap-x-6 gap-y-1 text-xs md:grid-cols-2">
          {settings.data.readonly.map((item) => (
            <div key={item.name} className="flex justify-between gap-3 border-b border-slate-50 py-1">
              <dt className="font-mono text-slate-500">{item.env}</dt>
              <dd className="truncate font-mono">{String(item.value ?? '') || '—'}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  )
}

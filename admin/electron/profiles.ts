// Validation of server profiles (pure functions, unit-tested).

const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

export function isLoopback(url: URL): boolean {
  return LOOPBACK.has(url.hostname.toLowerCase()) || url.hostname.startsWith('127.')
}

export type ProfileCheck = { ok: true; url: string } | { ok: false; error: string }

/**
 * Normalise a server URL and enforce the transport rules:
 * - https anywhere; plain http only on this machine, or on a remote profile the admin explicitly marked as a
 *   trusted LAN server (`allowInsecureHttp`), because the admin token would otherwise travel unencrypted.
 * - "local" profiles (which may start and stop processes) must point at this machine.
 */
export function checkProfileUrl(raw: string, kind: 'local' | 'remote', allowInsecureHttp = false): ProfileCheck {
  let url: URL
  try {
    url = new URL(raw.trim())
  } catch {
    return { ok: false, error: 'Enter a full URL such as https://search.example.edu or http://localhost:8000.' }
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return { ok: false, error: 'The URL must start with http:// or https://.' }
  }
  if (url.username || url.password || url.search || url.hash) {
    return { ok: false, error: 'Leave credentials, query and fragment out of the URL.' }
  }
  if (kind === 'local' && !isLoopback(url)) {
    return { ok: false, error: 'A local server must use localhost / 127.0.0.1. Use a remote profile for other hosts.' }
  }
  if (url.protocol === 'http:' && !isLoopback(url) && !allowInsecureHttp) {
    return {
      ok: false,
      error: 'Remote servers need https:// so the admin token is encrypted in transit.',
    }
  }
  return { ok: true, url: `${url.origin}${url.pathname.replace(/\/+$/, '')}` }
}

/** ADMIN_TOKEN from the text of a .env file (KEY=value lines; quotes optional). */
export function adminTokenFromEnv(text: string): string | null {
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?ADMIN_TOKEN\s*=\s*(.*)\s*$/.exec(line)
    if (match) {
      const value = match[1].replace(/^(['"])(.*)\1$/, '$2').trim()
      return value || null
    }
  }
  return null
}

/** API paths the renderer may call through the main process. */
export function isAllowedApiPath(path: string): boolean {
  return (path === '/health' || path.startsWith('/api/admin/')) && !path.includes('..')
}

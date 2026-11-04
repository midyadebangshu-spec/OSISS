// Helpers for the "public address" part of the Website page: check a domain and fill in the Caddy config that puts HTTPS
// in front of the OSISS API (the same file as deploy/Caddyfile.example). Pure, so it is tested in plain Node.

const HOSTNAME = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/i
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/
const IPV6 = /^[0-9a-f:]+$/i

/** An error message when `value` is not a domain name like search.example.edu, else null. A scheme or path is rejected. */
export function domainError(value: string): string | null {
  const text = value.trim()
  if (!text) return 'Enter your domain, for example search.example.edu.'
  if (/^[a-z]+:\/\//i.test(text) || text.includes('/')) return 'Enter only the domain name, without https:// or a path.'
  if (!HOSTNAME.test(text)) return `“${text}” is not a valid domain name.`
  return null
}

/** An error message when `value` is not an IP address (or empty, which means "no restriction"), else null. */
export function ipError(value: string): string | null {
  const text = value.trim()
  if (!text) return null
  if (IPV4.test(text)) return null
  if (text.includes(':') && IPV6.test(text) && text.length >= 2) return null
  return `“${text}” is not an IP address.`
}

/**
 * The Caddyfile for `domain` in front of an API on 127.0.0.1:`port`. With `adminIp`, requests to /api/admin/* from any
 * other address get 403 (the admin token still protects them either way).
 */
export function buildCaddyfile(domain: string, adminIp: string, port = 8000): string {
  const host = domain.trim().toLowerCase()
  const ip = adminIp.trim()
  const lines = [`${host} {`, '\tencode gzip', '']
  if (ip) {
    lines.push(
      '\t@admin_from_elsewhere {',
      '\t\tpath /api/admin/*',
      `\t\tnot remote_ip ${ip}`,
      '\t}',
      '\trespond @admin_from_elsewhere "Forbidden" 403',
      '',
    )
  }
  lines.push(`\treverse_proxy 127.0.0.1:${port}`, '}')
  return `${lines.join('\n')}\n`
}

/** The commands to run on the server, in order (the README's section 11.5 steps 6 and 7). */
export function publicSteps(domain: string): string[] {
  const host = domain.trim().toLowerCase()
  return [
    `# 1. At your domain registrar, create an A record for ${host} pointing to this server's public IP.`,
    `#    Wait until:  ping ${host}  shows that IP.`,
    '# 2. Install Caddy: https://caddyserver.com/docs/install',
    '# 3. Save the Caddyfile above as /etc/caddy/Caddyfile, then:',
    'sudo systemctl reload caddy',
    '# 4. Open ports 80 and 443 (and keep 22 for SSH):',
    'sudo ufw allow 22,80,443/tcp && sudo ufw enable',
    `# 5. Run the API on 127.0.0.1 only (API_HOST=127.0.0.1) and set the public address in Access & API keys: https://${host}`,
  ]
}

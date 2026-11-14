// The server installer (site/install.sh): where it lives and how to run it. Shared by the main process and the screens.

export const INSTALL_SCRIPT_URL = 'https://midyadebangshu-spec.github.io/OSISS/install.sh'
export const INSTALL_SCRIPT_SOURCE_URL = 'https://github.com/midyadebangshu-spec/OSISS/blob/main/site/install.sh'

export type InstallCommandOptions = {
  /** Let other computers reach the API (the admin app is elsewhere). Default: this computer only. */
  listenAll?: boolean
  /** Install a systemd service so the API runs at boot. */
  service?: boolean
  /** Do not install Node.js (the website cannot be built on that server). */
  noWebsite?: boolean
}

export function installFlags(options: InstallCommandOptions = {}): string[] {
  const flags: string[] = []
  if (options.listenAll) flags.push('--listen', 'all')
  if (options.service) flags.push('--service')
  if (options.noWebsite) flags.push('--no-website')
  return flags
}

/** The one-line command for a terminal on the server. */
export function serverInstallCommand(options: InstallCommandOptions = {}): string {
  const flags = installFlags(options)
  return `curl -fsSL ${INSTALL_SCRIPT_URL} | bash${flags.length ? ` -s -- ${flags.join(' ')}` : ''}`
}

/** The command for a downloaded copy of the script. */
export function serverInstallFileCommand(options: InstallCommandOptions = {}, file = 'install.sh'): string {
  return ['bash', file, ...installFlags(options)].join(' ')
}

/** null when the folder is acceptable for an install; otherwise the reason. Mirrors the script's own checks. */
export function validateInstallDir(dir: string): string | null {
  const value = dir.trim()
  if (!value) return 'Choose a folder.'
  if (!value.startsWith('/')) return 'The folder must be an absolute path, for example /home/you/osiss.'
  if (value.includes('\n')) return 'The folder name must not contain a line break.'
  const clean = value.replace(/\/+$/, '') || '/'
  if (['/', '/usr', '/bin', '/sbin', '/etc', '/boot', '/dev', '/proc', '/sys', '/var', '/lib', '/root'].includes(clean) || clean.startsWith('/usr/')) {
    return `Refusing to install into ${clean}.`
  }
  return null
}

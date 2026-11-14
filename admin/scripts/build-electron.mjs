// Bundles the Electron main and preload scripts (TypeScript) into dist-electron/*.cjs.
import { pathToFileURL } from 'node:url'
import { build, context } from 'esbuild'

const options = {
  entryPoints: { main: 'electron/main.ts', preload: 'electron/preload.ts' },
  outdir: 'dist-electron',
  outExtension: { '.js': '.cjs' },
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  external: ['electron'],
  sourcemap: true,
  logLevel: 'info',
}

export async function watchElectron(onRebuild) {
  const ctx = await context({
    ...options,
    plugins: [{ name: 'notify', setup: (b) => b.onEnd((result) => result.errors.length || onRebuild()) }],
  })
  await ctx.watch()
  return ctx
}

// pathToFileURL, not a string template: on Windows `file://${path}` never equals import.meta.url, so the bundle was skipped.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await build(options)
}

// Bundles the Electron main and preload scripts (TypeScript) into dist-electron/*.cjs.
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

if (import.meta.url === `file://${process.argv[1]}`) {
  await build(options)
}

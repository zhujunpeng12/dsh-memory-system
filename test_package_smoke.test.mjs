// Published-package smoke test: exercise the artifact, not the checkout.
// This catches self-dependencies, omitted files and ESM imports that happen to
// resolve in a developer workspace but fail in a clean DSH profile.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'

const PACKAGE_NAME = '@zhujunpeng12/dsh-memory-system'

function npm(args, options) {
  const cli = process.env.npm_execpath
  if (cli) return execFileSync(process.execPath, [cli, ...args], options)
  if (process.platform === 'win32') {
    const launchers = execFileSync('where.exe', ['npm.cmd'], { encoding: 'utf8' })
      .split(/\r?\n/).filter(Boolean)
    for (const launcher of launchers) {
      const candidate = join(dirname(launcher), 'node_modules', 'npm', 'bin', 'npm-cli.js')
      if (existsSync(candidate)) return execFileSync(process.execPath, [candidate, ...args], options)
    }
  }
  return execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', args, {
    ...options,
    shell: process.platform === 'win32',
  })
}

test('packed artifact installs and imports in an empty project', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-memory-package-smoke-'))
  const packDir = join(root, 'pack')
  const consumerDir = join(root, 'consumer')
  mkdirSync(packDir)
  mkdirSync(consumerDir)
  try {
    const packed = JSON.parse(npm([
      'pack', '--json', '--pack-destination', packDir,
    ], { cwd: import.meta.dirname, encoding: 'utf8' }))
    assert.equal(packed.length, 1)
    assert.ok(packed[0].files.some(file => file.path === 'docs/memory-system-flowchart.html'))
    for (const doc of ['CONTRIBUTING.md', 'SECURITY.md']) {
      assert.ok(packed[0].files.some(file => file.path === doc), `${doc} must be included in the package`)
    }
    const tarball = join(packDir, packed[0].filename)

    writeFileSync(join(consumerDir, 'package.json'), JSON.stringify({
      name: 'dsh-memory-package-smoke-consumer',
      private: true,
      type: 'module',
    }))
    npm([
      'install', '--ignore-scripts', '--no-audit', '--no-fund', '--no-package-lock', tarball,
    ], { cwd: consumerDir, stdio: 'pipe' })
    for (const doc of ['CONTRIBUTING.md', 'SECURITY.md']) {
      assert.ok(existsSync(join(consumerDir, 'node_modules', PACKAGE_NAME, doc)), `${doc} must be installed`)
    }

    const result = JSON.parse(execFileSync(process.execPath, [
      '--input-type=module',
      '--eval',
      `const m = await import('${PACKAGE_NAME}'); console.log(JSON.stringify({ name: m.name, inject: m.inject, apply: typeof m.apply }))`,
    ], { cwd: consumerDir, encoding: 'utf8' }))
    assert.deepEqual(result, {
      name: 'dsh-memory-system',
      inject: ['tools', 'agents'],
      apply: 'function',
    })

    // Exercise the shipped CLI and support files, not a checkout-only demo.
    const demo = JSON.parse(execFileSync(process.env.PYTHON || 'python', [
      join(consumerDir, 'node_modules', PACKAGE_NAME, 'vault-guard', 'demo_memory.py'), '--json',
    ], { cwd: consumerDir, encoding: 'utf8', timeout: 30_000 }))
    assert.equal(demo.synthetic, true)
    assert.equal(demo.configured_user_vault_used, false)
    assert.equal(demo.live_dsh_host_tested, false)
    assert.equal(demo.default_recall.hits, 0)
    assert.equal(demo.effective_recall.old_fact_visible, false)
    assert.equal(demo.temporary_vault_removed, true)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

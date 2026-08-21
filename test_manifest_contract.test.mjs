// DSH packaging contract tests.
// These checks intentionally avoid a YAML dependency: the bundle patch is tiny,
// and exact text assertions catch the scoped-name/injection drift that broke
// GitHub installs while the previous unit suite remained green.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { apply } from './index.js'

const ROOT = dirname(fileURLToPath(import.meta.url))
const PACKAGE_NAME = '@zhujunpeng12/dsh-memory-system'
const read = (name) => readFileSync(join(ROOT, name), 'utf8')

test('package metadata has no self dependency or host-runtime dependency', () => {
  const pkg = JSON.parse(read('package.json'))
  assert.equal(pkg.name, PACKAGE_NAME)
  assert.equal(pkg.version, '0.1.1')
  assert.equal(pkg.dsh?.bundle?.patch, './cordis.patch.yml')
  assert.equal(pkg.dependencies?.[PACKAGE_NAME], undefined, 'a package must never depend on its own published version')
  assert.equal(pkg.dependencies, undefined)
  assert.equal(pkg.peerDependencies, undefined)
})

test('Cordis bundle uses the installable scoped package and injects required services', () => {
  const patch = read('cordis.patch.yml')
  assert.match(patch, /name:\s*['"]@zhujunpeng12\/dsh-memory-system['"]/)
  assert.match(patch, /inject:\s*\[[^\]]*tools[^\]]*agents[^\]]*\]/)
})

test('legacy manifest and runtime export agree on tools + agents injection', () => {
  const manifest = JSON.parse(read('dsh.plugin.json'))
  assert.equal(manifest.name, PACKAGE_NAME)
  assert.equal(manifest.version, '0.1.1')
  assert.equal(manifest.entry?.name, PACKAGE_NAME)
  assert.deepEqual(manifest.entry?.inject, ['tools', 'agents'])

  const source = read('index.js')
  assert.match(source, /export const inject\s*=\s*\['tools',\s*'agents'\]/)
  assert.match(source, /ctx\.agents\.roots\(\)/)
  assert.doesNotMatch(source, /from\s+['"]@deepseek-ai\//, 'published plugin must not import host packages by path')
})

test('import-free tool definitions preserve required/type/enum validation', async () => {
  const registered = []
  const agent = {
    ctx: {
      tools: { register(definition) { registered.push(definition); return () => {} } },
      effect(factory) { return factory() },
    },
  }
  const ctx = {
    agents: { roots: () => [agent] },
    on() { return () => {} },
    effect() { return () => {} },
  }
  apply(ctx)
  assert.equal(registered.length, 6)

  const recall = registered.find(tool => tool.name === 'memory_recall')
  const write = registered.find(tool => tool.name === 'memory_write')
  const exec = { agent, signal: new AbortController().signal }
  await assert.rejects(() => recall.execute({}, exec), /missing required argument query/)
  await assert.rejects(() => recall.execute({ query: 'x', top: 1.5 }, exec), /top must be integer/)
  await assert.rejects(() => write.execute({ op: 'delete' }, exec), /op must be one of/)
  await assert.rejects(() => recall.execute({ query: 'x', extra: true }, exec), /unknown argument extra/)
})

test('README puts a runnable install path near the top', () => {
  for (const file of ['README.md', 'README.en.md']) {
    const first120 = read(file).split(/\r?\n/).slice(0, 120).join('\n')
    assert.match(first120, /npx @deepseek-ai\/dsh plugin --profile web add/)
  }
})

test('architecture docs describe the native V3 plugin path', () => {
  for (const file of ['README.md', 'README.en.md']) {
    const source = read(file)
    assert.doesNotMatch(source, /SessionStart Hook/)
    assert.match(source, /agent\/pre-step/)
    assert.match(source, /memory-system-flowchart\.html/)
  }
  const diagram = read('docs/memory-system-flowchart.html')
  assert.match(diagram, /架构图[^<]*<span class="version">· V3/)
  assert.match(diagram, /inject: tools \+ agents/)
  assert.match(diagram, /memory_write · apply=true/)
})

test('GitHub social preview keeps the recommended shape and upload budget', () => {
  const previewPath = join(ROOT, 'assets/social-preview.png')
  const preview = readFileSync(previewPath)
  assert.equal(preview.subarray(1, 4).toString('ascii'), 'PNG')
  assert.equal(preview.readUInt32BE(16), 1280)
  assert.equal(preview.readUInt32BE(20), 640)
  assert.ok(statSync(previewPath).size < 1_000_000, 'GitHub social preview must stay below 1 MB')
})

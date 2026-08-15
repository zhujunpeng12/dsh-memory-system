// test_plugin_bridge.test.mjs — 插件参数桥接契约测试
//
// 覆盖 index.js/bridge.js 里「工具参数 → Python CLI argv」的映射。这是 B1 类
// bug（工具传脚本不认识的 flag）的唯一防线——之前测试套件没覆盖它，CI 全绿
// 也抓不到 memory_write 与 vault-write.py 的参数错位。
//
// 两层验证：
//   1) 纯 JS 断言：buildToolInvocation 对 6 个工具的输出（script 名、参数名、
//      参数顺序、memory_write 的必填校验与 tempFiles 语义）。
//   2) 跨语言契约：调 Python 脚本的 --help 动态解析 add_argument，断言 bridge
//      发出的每个 flag 在 Python 侧真实存在 —— JS 改 flag 或 Python 改 argparse
//      任一侧漂移都会在此失败。

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { buildToolInvocation, TMPFILE_MARKER } from './bridge.js'

const ROOT = dirname(fileURLToPath(import.meta.url))
const GUARD = join(ROOT, 'vault-guard')
const PYTHON = process.env.PYTHON ?? 'python'

// 从某个 Python 脚本的 --help 输出提取它接受的全部长 flag（--xxx）。
// extraArgs 用于有子命令的脚本（如 vault-write.py raw --help）。
function pythonFlags(script, extraArgs = []) {
  const out = execFileSync(PYTHON, [join(GUARD, script), ...extraArgs, '--help'], { encoding: 'utf8' })
  const flags = new Set()
  for (const line of out.split('\n')) {
    const m = line.match(/--[a-z][a-z0-9-]*/)
    if (m) flags.add(m[0])
  }
  return flags
}

// 从 invocation argv 提取全部长 flag（跳过位置参数与值）。
function jsFlags(argv) {
  return new Set(argv.filter((a) => typeof a === 'string' && a.startsWith('--')))
}

// 慢速但关键的跨语言契约：每个工具的 script 发出的 flag 必须存在于对应
// Python 脚本的 argparse 定义中。带 helperScriptArgs 的用例会先给脚本传子命令。
const CONTRACT_CASES = [
  ['memory_bootstrap', 'bootstrap.py', [], { cwd: '/w', maxBytes: 14000 }],
  ['memory_recall', 'recall.py', [], { query: 'q', cwd: '/w', force: true, top: 8 }],
  ['memory_gate', 'check.py', [], { closing: true, expectWrite: true }],
  ['memory_govern', 'govern.py', [], { json: true, maxItems: 30 }],
  ['memory_trajectory_review', 'trajectory-review.py', [], { cwd: '/w', days: 14, maxSessions: 20, maxItems: 20, minToolErrors: 3, allWorkspaces: true, json: true }],
  ['memory_write', 'vault-write.py', ['raw'], { op: 'raw', title: 't', body: 'b', source: 's', kind: 'correction', date: '2026-08-15', supersedes: 'id1', apply: true }],
  ['memory_write', 'vault-write.py', ['replace'], { op: 'replace', target: 'memory/rules.md', newText: 'x', expectedSha256: 'abc', apply: true }],
  ['memory_write', 'vault-write.py', ['recover'], { op: 'recover', apply: true }],
]

test('跨语言契约：每个工具发出的 flag 都存在于对应 Python argparse', () => {
  for (const [tool, script, helperArgs, args] of CONTRACT_CASES) {
    const inv = buildToolInvocation(tool, args, '/tmp')
    assert.equal(inv.script, script, `${tool} 应调用 ${script}`)
    assert.ok(!inv.error, `${tool}/${JSON.stringify(args)} 不应报错: ${inv.error}`)
    const python = pythonFlags(script, helperArgs)
    const emitted = jsFlags(inv.argv)
    for (const flag of emitted) {
      assert.ok(python.has(flag), `${tool} 发出 ${flag}，但 ${script} ${helperArgs.join(' ')} 的 argparse 没有该 flag（B1 类漂移）`)
    }
  }
})

test('memory_write 特殊：raw 走 --body-file 文件桥，tempFiles 一一对应', () => {
  const inv = buildToolInvocation('memory_write', { op: 'raw', title: '标题', body: '这是正文', source: 'sess-1', kind: 'fact' }, '/tmp')
  assert.equal(inv.script, 'vault-write.py')
  assert.equal(inv.argv[0], 'raw')
  assert.ok(inv.argv.includes('--title') && inv.argv.includes('标题'))
  assert.ok(inv.argv.includes('--body-file'))
  assert.ok(inv.argv.includes(TMPFILE_MARKER), 'raw 正文应走临时文件 marker')
  assert.ok(inv.argv.includes('--source') && inv.argv.includes('sess-1'))
  assert.ok(inv.argv.includes('--kind') && inv.argv.includes('fact'))
  assert.equal(inv.tempFiles.length, 1)
  assert.equal(inv.tempFiles[0].content, '这是正文')
})

test('memory_write：raw 缺必填参数时报错且不发参', () => {
  assert.match(buildToolInvocation('memory_write', { op: 'raw', title: 't' }, '/tmp').error, /title, body and source/)
  assert.match(buildToolInvocation('memory_write', { op: 'raw', title: 't', body: 'b' }, '/tmp').error, /title, body and source/)
  assert.match(buildToolInvocation('memory_write', { op: 'raw', body: 'b', source: 's' }, '/tmp').error, /title, body and source/)
})

test('memory_write：replace 缺必填参数时报错', () => {
  assert.match(buildToolInvocation('memory_write', { op: 'replace', target: 'f.md' }, '/tmp').error, /target and newText/)
  assert.match(buildToolInvocation('memory_write', { op: 'replace', newText: 'x' }, '/tmp').error, /target and newText/)
})

test('memory_write：correction/tombstone 需带 supersedes 才生效（kind 枚举透传）', () => {
  const inv = buildToolInvocation('memory_write', { op: 'raw', title: 't', body: 'b', source: 's', kind: 'tombstone', supersedes: 'old-1', apply: true }, '/tmp')
  assert.ok(inv.argv.includes('--kind') && inv.argv.includes('tombstone'))
  assert.ok(inv.argv.includes('--supersedes') && inv.argv.includes('old-1'))
  assert.ok(inv.argv.includes('--apply'))
})

test('memory_write：recover 无必填、仅可带 --apply', () => {
  const inv = buildToolInvocation('memory_write', { op: 'recover', apply: true }, '/tmp')
  assert.deepEqual(inv.argv, ['recover', '--apply'])
  assert.equal(inv.tempFiles, undefined)
})

test('memory_write：apply 缺省时不发 --apply（dry-run 默认）', () => {
  const inv = buildToolInvocation('memory_write', { op: 'raw', title: 't', body: 'b', source: 's' }, '/tmp')
  assert.ok(!inv.argv.includes('--apply'))
})

test('布尔参数只在真值时发出 flag', () => {
  assert.ok(!buildToolInvocation('memory_gate', {}, '/tmp').argv.includes('--closing'))
  assert.ok(!buildToolInvocation('memory_govern', {}, '/tmp').argv.includes('--json'))
  assert.ok(!buildToolInvocation('memory_recall', { query: 'q' }, '/tmp').argv.includes('--force'))
})

test('数值参数转字符串传递', () => {
  const inv = buildToolInvocation('memory_bootstrap', { cwd: '/w', maxBytes: 14000 }, '/tmp')
  const i = inv.argv.indexOf('--max-bytes')
  assert.equal(inv.argv[i + 1], '14000')
})

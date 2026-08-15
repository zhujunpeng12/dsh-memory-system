// dsh-vault-memory — local-first persistent memory infrastructure for DSH
//
// Host plugin. Registers five agent tools that drive the Python scripts in
// ./vault-guard: bootstrap (hot packet), recall (cold recall), gate (mechanical
// checks), govern (read-only governance candidates) and write (authorized
// transactional writes, dry-run by default).
//
// All memory content lives in the user's own Obsidian Vault (MEMORY_VAULT),
// never inside this plugin. Zero external npm dependencies; only Node built-ins
// plus the Python standard library.
//
// Changing index.js requires a Harness restart to take effect (module code is
// not hot-reloaded).

import { spawn } from 'node:child_process'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'dsh-vault-memory'
export const inject = ['tools']

const HERE = dirname(fileURLToPath(import.meta.url))
const GUARD = join(HERE, 'vault-guard')
const PYTHON = process.env.PYTHON ?? 'python'
const MAX_OUTPUT = 2 * 1024 * 1024
const RUN_TIMEOUT_MS = 60_000

// Tools that mutate the memory vault require explicit confirmation.
const MUTATING_TOOLS = new Set(['memory_write'])

const JSON_OUTPUT = {
  schema: { type: 'json' },
  render(_args, value) { return [{ type: 'text', text: JSON.stringify(value) }] },
}

function json(value) {
  return JSON.parse(JSON.stringify(value))
}

function runEnv() {
  return {
    ...process.env,
    // Empty values fall back to each script's default (home / ".dsh",
    // home / "Documents" / "Obsidian Vault").
    MEMORY_VAULT: process.env.MEMORY_VAULT ?? '',
    DSH_HOME: process.env.DSH_HOME ?? '',
  }
}

// Run one vault-guard script with args. Resolves {ok, code, stdout, stderr}.
function runScript(script, args = [], { timeoutMs = RUN_TIMEOUT_MS } = {}) {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [join(GUARD, script), ...args], {
      env: runEnv(),
      windowsHide: true,
    })
    let out = ''
    let err = ''
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      child.kill()
    }, timeoutMs)
    child.stdout.on('data', (chunk) => {
      out += chunk
      if (out.length > MAX_OUTPUT) child.kill()
    })
    child.stderr.on('data', (chunk) => {
      err += chunk
    })
    child.on('error', (error) => {
      clearTimeout(timer)
      resolve({ ok: false, code: -1, timedOut, stdout: out, stderr: String(error) })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ ok: code === 0, code, timedOut, stdout: out, stderr: err })
    })
  })
}

function present(title, kind, rawInput) {
  return { card: 'generic', title, kind, ...(rawInput === undefined ? {} : { rawInput }) }
}

// Text output tools return {ok, text} (stdout is already UTF-8 safe).
function textResult(run) {
  if (run.timedOut) return { ok: false, error: 'timeout' }
  const text = (run.stdout || '').trim()
  if (!run.ok) return { ok: false, code: run.code, stderr: (run.stderr || '').trim() }
  return { ok: true, text }
}

// Gate/govern emit machine-readable JSON when asked; keep both paths.
function jsonResult(run, fallbackKey = 'text') {
  if (run.timedOut) return { ok: false, error: 'timeout' }
  const text = (run.stdout || '').trim()
  if (!run.ok) return { ok: false, code: run.code, stderr: (run.stderr || '').trim() }
  try {
    return { ok: true, value: JSON.parse(text) }
  } catch {
    return { ok: true, [fallbackKey]: text }
  }
}

function registerAgentTools(agent) {
  const disposers = []
  const register = (definition) => disposers.push(agent.ctx.tools.register(definition))
  const guard = (exec, agent) => {
    if (exec.agent !== agent || exec.signal.aborted) return null
    return exec
  }

  register(defineTool({
    name: 'memory_bootstrap',
    description:
      'Generate the bounded hot-memory packet (gate status, instruction budget, user profile, active core rules, current project summary, recent event headings) for the given working directory. Use at session start when a memory vault is configured.',
    parameters: {
      cwd: { type: 'string', description: 'Working directory to match the project note in the memory vault. Defaults to the session cwd.' },
      maxBytes: { type: 'integer', description: 'Packet byte budget. Defaults to 14000.' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = ['--cwd', args.cwd ?? exec.ctx?.cwd ?? process.cwd()]
      if (args.maxBytes) argv.push('--max-bytes', String(args.maxBytes))
      return json(textResult(await runScript('bootstrap.py', argv)))
    },
    presentCall: (args) => present('Generate hot-memory packet', 'read', args.cwd),
  }))

  register(defineTool({
    name: 'memory_recall',
    description:
      'Explainable cold recall over the memory vault: exact + Chinese bigram BM25 + metadata rerank, bounded to ~4.2KB. Use when the user references past history, corrections, or a concrete topic that the hot packet did not cover.',
    parameters: {
      query: { type: 'string', required: true, description: 'The question or topic to recall.' },
      cwd: { type: 'string', description: 'Working directory used for project reranking. Defaults to the session cwd.' },
      force: { type: 'boolean', description: 'Skip the trigger heuristics and always run recall.' },
      top: { type: 'integer', description: 'Maximum hits to consider. Defaults to 8.' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = ['--query', args.query, '--cwd', args.cwd ?? exec.ctx?.cwd ?? process.cwd()]
      if (args.force) argv.push('--force')
      if (args.top) argv.push('--top', String(args.top))
      return json(textResult(await runScript('recall.py', argv)))
    },
    presentCall: (args) => present('Cold recall', 'read', args.query),
  }))

  register(defineTool({
    name: 'memory_gate',
    description:
      'Run the mechanical gate checks over the memory vault: raw-to-events gaps, size limits, rules-core sync, lock state. Read-only. Exit code 0 = all clear; 1 = gaps found.',
    parameters: {
      closing: { type: 'boolean', description: 'Closing-session mode: only check whether existing raw entries were distilled.' },
      expectWrite: { type: 'boolean', description: 'With closing: also require today\'s raw entry to exist (authorized-write closing).' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 60_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = []
      if (args.closing) argv.push('--closing')
      if (args.expectWrite) argv.push('--expect-write')
      return json(textResult(await runScript('check.py', argv)))
    },
    presentCall: () => present('Run memory gate', 'read'),
  }))

  register(defineTool({
    name: 'memory_govern',
    description:
      'Read-only governance scan of the memory vault: duplicate/conflicting/stale/oversized candidates, rule lifecycle, transaction anomalies. Collects evidence and suggestions; never writes.',
    parameters: {
      json: { type: 'boolean', description: 'Emit machine-readable JSON.' },
      maxItems: { type: 'integer', description: 'Maximum findings. Defaults to 100.' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = []
      if (args.json) argv.push('--json')
      if (args.maxItems) argv.push('--max-items', String(args.maxItems))
      return json(jsonResult(await runScript('govern.py', argv)))
    },
    presentCall: () => present('Scan governance candidates', 'read'),
  }))

  register(defineTool({
    name: 'memory_trajectory_review',
    description:
      'Read-only trajectory review: scan session traces (user corrections as the hard signal) plus the tool-call ledger from the evidence-ledger plugin, and produce candidate review items (scenario → error → root cause → precondition). Never writes the vault and never auto-judges.',
    parameters: {
      days: { type: 'integer', description: 'Lookback window in days. Defaults to 14.' },
      maxSessions: { type: 'integer', description: 'Maximum sessions to scan. Defaults to 20.' },
      maxItems: { type: 'integer', description: 'Maximum candidate items. Defaults to 20.' },
      minToolErrors: { type: 'integer', description: 'Minimum repeated tool errors to surface. Defaults to 3.' },
      cwd: { type: 'string', description: 'Only review the current workspace (default).' },
      allWorkspaces: { type: 'boolean', description: 'Review all workspaces instead of the current one.' },
      json: { type: 'boolean', description: 'Emit machine-readable JSON.' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = ['--cwd', args.cwd ?? exec.ctx?.cwd ?? process.cwd()]
      if (args.days) argv.push('--days', String(args.days))
      if (args.maxSessions) argv.push('--max-sessions', String(args.maxSessions))
      if (args.maxItems) argv.push('--max-items', String(args.maxItems))
      if (args.minToolErrors) argv.push('--min-tool-errors', String(args.minToolErrors))
      if (args.allWorkspaces) argv.push('--all-workspaces')
      if (args.json) argv.push('--json')
      return json(jsonResult(await runScript('trajectory-review.py', argv)))
    },
    presentCall: () => present('Scan trajectory review candidates', 'read'),
  }))

  register(defineTool({
    name: 'memory_write',
    description:
      'Authorized transactional write to the memory vault. Dry-run by default; only applies with apply=true after explicit user consent. Raw appends are append-only; corrections require --supersedes. Never edits historical raw entries in place.',
    parameters: {
      op: { type: 'string', enum: ['raw', 'replace', 'recover'], description: 'Operation: raw append, transactional replace of a non-raw file, or transaction recovery.' },
      target: { type: 'string', description: 'For replace: path (relative to vault) of the file to update.' },
      content: { type: 'string', description: 'For raw/replace: the content to append or the replacement text.' },
      entryId: { type: 'string', description: 'For raw: the entry id this raw belongs to (used for the receipt).' },
      supersedes: { type: 'string', description: 'For corrections: the exact entry id being superseded. Required for corrections; raw is append-only.' },
      expectedSha256: { type: 'string', description: 'For replace: the SHA-256 the current file must have (protects against concurrent edits).' },
      apply: { type: 'boolean', description: 'Actually apply the write. Without this, the tool only previews (dry-run).' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const argv = [args.op]
      if (args.target) argv.push('--target', args.target)
      if (args.content) argv.push('--content', args.content)
      if (args.entryId) argv.push('--entry-id', args.entryId)
      if (args.supersedes) argv.push('--supersedes', args.supersedes)
      if (args.expectedSha256) argv.push('--expected-sha256', args.expectedSha256)
      if (args.apply) argv.push('--apply')
      return json(textResult(await runScript('vault-write.py', argv)))
    },
    presentCall: (args) => present('Memory write', 'other', `${args.op}${args.apply ? ' (apply)' : ' (dry-run)'}`),
  }))

  return () => { for (const dispose of disposers.reverse()) dispose() }
}

export function apply(ctx) {
  const agentTools = new Map()
  const mount = (agent) => {
    if (agentTools.has(agent) || !ctx.agents.roots().includes(agent)) return
    const dispose = agent.ctx.effect(() => registerAgentTools(agent), 'dsh-vault-memory: agent tools')
    agentTools.set(agent, dispose)
  }
  for (const agent of ctx.agents.roots()) mount(agent)
  ctx.on('agent/created', ({ agent }) => mount(agent))
  ctx.on('agent/disposed', ({ agent }) => { agentTools.delete(agent) })
  ctx.on('tools/pre-execute', async (exec, next) => {
    const downstream = await next()
    if (downstream.kind !== 'allow' || !MUTATING_TOOLS.has(exec.name) || !agentTools.has(exec.agent)) return downstream
    return {
      kind: 'ask',
      reason: 'memory_write mutates the memory vault. Review the exact target and content; it is dry-run unless apply=true.',
    }
  })
  ctx.effect(() => () => {
    for (const dispose of [...agentTools.values()].reverse()) dispose()
    agentTools.clear()
  }, 'dsh-vault-memory: cleanup')
}

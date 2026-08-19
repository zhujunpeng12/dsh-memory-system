// dsh-memory-system — local-first persistent memory infrastructure for DSH
//
// Host plugin. Registers six agent tools that drive the Python scripts in
// ./vault-guard: bootstrap (hot packet), recall (cold recall), gate (mechanical
// checks), govern (read-only governance candidates), trajectory review
// (evidence-driven candidates) and write (authorized transactional writes,
// dry-run by default).
//
// Also injects the hot-memory packet automatically: on each new session's
// first pre-step it runs bootstrap.py and prepends the packet to the model
// context (zero manual hooks configuration). Disable with
// DSH_MEMORY_AUTO_INJECT=false.
//
// Memory content lives in a plain local directory by default (~/.dsh-memory,
// no Obsidian required); set MEMORY_VAULT to point at an Obsidian Vault to
// switch to vault mode. First run auto-initializes the skeleton. Zero external
// npm dependencies; only Node built-ins plus the Python standard library.
//
// Changing index.js requires a Harness restart to take effect (module code is
// not hot-reloaded).

import { spawn } from 'node:child_process'
import { writeFileSync, rmSync, mkdtempSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { buildToolInvocation, TMPFILE_MARKER } from './bridge.js'

export const name = 'dsh-memory-system'
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
    // home / ".dsh-memory").
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

// Write inline text to a temp file and return its path + a cleanup disposer.
// vault-write.py takes content as file paths (--body-file / --source-file),
// so the plugin bridges agent-friendly inline content to the script's
// file-driven CLI. The temp dir lives outside the vault and is removed after
// the subprocess returns.
function withTempFile(content, suffix = '.md') {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-memory-system-'))
  const file = join(dir, `content-${Date.now()}-${Math.random().toString(36).slice(2)}${suffix}`)
  writeFileSync(file, content, 'utf8')
  return { file, dispose: () => { try { rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ } } }
}

// Materialize an invocation returned by buildToolInvocation: write each temp
// file and replace TMPFILE_MARKER in argv with the real path. Returns the
// concrete argv plus disposers (closed by the caller).
function resolveInvocation(inv) {
  const argv = [...inv.argv]
  const disposers = []
  if (inv.tempFiles) {
    for (const tf of inv.tempFiles) {
      const tmp = withTempFile(tf.content)
      disposers.push(tmp.dispose)
      for (let i = 0; i < argv.length; i++) {
        if (argv[i] === TMPFILE_MARKER) argv[i] = tmp.file
      }
    }
  }
  return { argv, disposers }
}

function present(title, kind, rawInput) {
  return { card: 'generic', title, kind, ...(rawInput === undefined ? {} : { rawInput }) }
}

// Text output tools return {ok, text} (stdout is already UTF-8 safe).
function textResult(run) {
  if (run.timedOut) return { ok: false, error: 'timeout' }
  const text = (run.stdout || '').trim()
  if (!run.ok) return { ok: false, code: run.code, stdout: text, stderr: (run.stderr || '').trim() }
  return { ok: true, text }
}

// Gate/govern emit machine-readable JSON when asked; keep both paths.
function jsonResult(run, fallbackKey = 'text') {
  if (run.timedOut) return { ok: false, error: 'timeout' }
  const text = (run.stdout || '').trim()
  if (!run.ok) return { ok: false, code: run.code, stdout: text, stderr: (run.stderr || '').trim() }
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
      const inv = buildToolInvocation('memory_bootstrap', args, exec.ctx?.cwd)
      return json(textResult(await runScript(inv.script, inv.argv)))
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
      const inv = buildToolInvocation('memory_recall', args, exec.ctx?.cwd)
      return json(textResult(await runScript(inv.script, inv.argv)))
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
      const inv = buildToolInvocation('memory_gate', args, exec.ctx?.cwd)
      return json(textResult(await runScript(inv.script, inv.argv)))
    },
    presentCall: () => present('Run memory gate', 'read'),
  }))

  register(defineTool({
    name: 'memory_govern',
    description:
      'Read-only governance scan of the memory vault: duplicate/conflicting/stale/oversized candidates, rule lifecycle, transaction anomalies. Collects evidence and suggestions; never writes.',
    parameters: {
      json: { type: 'boolean', description: 'Emit machine-readable JSON.' },
      maxItems: { type: 'integer', description: 'Maximum findings. Defaults to 30.' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const inv = buildToolInvocation('memory_govern', args, exec.ctx?.cwd)
      return json(jsonResult(await runScript(inv.script, inv.argv)))
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
      const inv = buildToolInvocation('memory_trajectory_review', args, exec.ctx?.cwd)
      return json(jsonResult(await runScript(inv.script, inv.argv)))
    },
    presentCall: () => present('Scan trajectory review candidates', 'read'),
  }))

  register(defineTool({
    name: 'memory_write',
    description:
      'Authorized transactional write to the memory vault. Dry-run by default; only applies with apply=true after explicit user consent. Raw appends are append-only; corrections require kind=correction|tombstone plus supersedes. Never edits historical raw entries in place.',
    parameters: {
      op: { type: 'string', enum: ['raw', 'replace', 'recover'], description: 'Operation: raw append, transactional replace of a non-raw file, or transaction recovery.' },
      // raw subcommand:
      title: { type: 'string', description: 'For raw: the raw entry title (required).' },
      body: { type: 'string', description: 'For raw: the factual body text (facts only, no evaluation).' },
      source: { type: 'string', description: 'For raw: where the evidence came from (session id, file path, url).' },
      kind: { type: 'string', enum: ['fact', 'correction', 'tombstone'], description: 'For raw: entry kind. Defaults to fact. Corrections must set supersedes.' },
      date: { type: 'string', description: 'For raw: YYYY-MM-DD for the raw file. Defaults to today.' },
      supersedes: { type: 'string', description: 'For raw corrections: the exact entry id being superseded (required for correction/tombstone).' },
      // replace subcommand:
      target: { type: 'string', description: 'For replace: absolute or vault-relative path of the non-raw file to update.' },
      newText: { type: 'string', description: 'For replace: the full replacement text (written to a temp file, then applied transactionally).' },
      expectedSha256: { type: 'string', description: 'For replace: the SHA-256 the current file must have (protects against concurrent edits).' },
      apply: { type: 'boolean', description: 'Actually apply the write. Without this, the tool only previews (dry-run).' },
    },
    output: JSON_OUTPUT,
    timeoutMs: 120_000,
    async execute(args, exec) {
      if (!guard(exec, agent)) return json({ ok: false, code: 'cancelled' })
      const inv = buildToolInvocation('memory_write', args, exec.ctx?.cwd)
      if (inv.error) return json({ ok: false, error: inv.error })
      const { argv, disposers } = resolveInvocation(inv)
      try {
        return json(textResult(await runScript(inv.script, argv)))
      } finally {
        for (const dispose of disposers) dispose()
      }
    },
    presentCall: (args) => present('Memory write', 'other', `${args.op}${args.apply ? ' (apply)' : ' (dry-run)'}`),
  }))

  return () => { for (const dispose of disposers.reverse()) dispose() }
}

export function apply(ctx) {
  const agentTools = new Map()
  const mount = (agent) => {
    if (agentTools.has(agent) || !ctx.agents.roots().includes(agent)) return
    const dispose = agent.ctx.effect(() => registerAgentTools(agent), 'dsh-memory-system: agent tools')
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

  // 热包自动注入（零手动 hooks 配置）：每个新会话的首轮 pre-step 注入一次
  // bootstrap 热包。按 session.header.id 去重（agent 持久跨会话，session 会变）；
  // DSH_MEMORY_AUTO_INJECT=false 可关闭。
  // 与 dsh-hooks-claude-code 的 UserPromptSubmit 映射到 agent/pre-step 同一机制，
  // 但原生实现，不依赖任何 hooks 配置。
  const PLUGIN_SOURCE = { kind: 'plugin', plugin: 'dsh-memory-system' }
  const injectedSessions = new Set() // sessionId -> injected
  const AUTO_INJECT = process.env.DSH_MEMORY_AUTO_INJECT !== 'false'
  ctx.on('agent/pre-step', async ({ agent, messages, turn, signal }, next) => {
    if (!AUTO_INJECT || messages.length === 0) return next()
    const header = agent?.session?.header
    const sid = header?.id
    if (!sid || injectedSessions.has(sid)) return next()
    try {
      const cwd = typeof header?.cwd === 'string' && header.cwd !== '' ? header.cwd : process.cwd()
      const run = await runScript('bootstrap.py', ['--cwd', cwd], { timeoutMs: 60_000 })
      if (run.ok && run.stdout.trim()) {
        injectedSessions.add(sid)
        if (injectedSessions.size > 2000) injectedSessions.clear() // 安全阀
        const ours = createUserMessage({
          content: [{ type: 'text', text: run.stdout.trim() }],
          source: PLUGIN_SOURCE,
        })
        const downstream = await next()
        if (downstream.kind !== 'enter') return downstream
        return { kind: 'enter', messages: [...downstream.messages, ours] }
      }
    } catch {
      // 注入失败静默：记忆是增强，不能打断会话
    }
    return next()
  })

  ctx.effect(() => () => {
    for (const dispose of [...agentTools.values()].reverse()) dispose()
    agentTools.clear()
  }, 'dsh-memory-system: cleanup')
}

// bridge.js — tool argument → Python CLI argv mapping (pure, zero-dependency)
//
// This is the single source of truth for how each agent tool maps its
// camelCase input parameters onto the kebab-case argparse flags of the
// vault-guard Python scripts. Keeping it dependency-free lets
// test_plugin_bridge.test.mjs exercise the mapping directly (the B1 class of
// bug — a tool passing flags the script does not accept — was previously
// invisible to the test suite and CI).
//
// Changing a flag here without changing the matching Python script (or vice
// versa) should fail test_plugin_bridge.

export const TMPFILE_MARKER = '__VAULT_WRITE_TMPFILE__'

/**
 * Map one agent tool's parameters to { script, argv, tempFiles? }.
 * argv may contain TMPFILE_MARKER where a caller-provided inline string must
 * be written to a temp file first (see memory_write); tempFiles lists the
 * contents to write, in the same order markers appear in argv.
 */
export function buildToolInvocation(toolName, args = {}, cwd = '') {
  const here = cwd || process.cwd()
  switch (toolName) {
    case 'memory_bootstrap': {
      const argv = ['--cwd', args.cwd ?? here]
      if (args.maxBytes) argv.push('--max-bytes', String(args.maxBytes))
      return { script: 'bootstrap.py', argv }
    }
    case 'memory_recall': {
      const argv = ['--query', args.query, '--cwd', args.cwd ?? here]
      if (args.force) argv.push('--force')
      if (args.top) argv.push('--top', String(args.top))
      return { script: 'recall.py', argv }
    }
    case 'memory_gate': {
      const argv = []
      if (args.closing) argv.push('--closing')
      if (args.expectWrite) argv.push('--expect-write')
      return { script: 'check.py', argv }
    }
    case 'memory_govern': {
      const argv = []
      if (args.json) argv.push('--json')
      if (args.maxItems) argv.push('--max-items', String(args.maxItems))
      return { script: 'govern.py', argv }
    }
    case 'memory_trajectory_review': {
      const argv = ['--cwd', args.cwd ?? here]
      if (args.days) argv.push('--days', String(args.days))
      if (args.maxSessions) argv.push('--max-sessions', String(args.maxSessions))
      if (args.maxItems) argv.push('--max-items', String(args.maxItems))
      if (args.minToolErrors) argv.push('--min-tool-errors', String(args.minToolErrors))
      if (args.allWorkspaces) argv.push('--all-workspaces')
      if (args.json) argv.push('--json')
      return { script: 'trajectory-review.py', argv }
    }
    case 'memory_write':
      return buildWriteInvocation(args)
    default:
      throw new Error(`unknown tool: ${toolName}`)
  }
}

function buildWriteInvocation(args) {
  if (args.op === 'raw') {
    if (!args.title || !args.body || !args.source) {
      return { error: 'raw requires title, body and source' }
    }
    const argv = ['raw', '--title', args.title, '--body-file', TMPFILE_MARKER, '--source', args.source]
    if (args.kind) argv.push('--kind', args.kind)
    if (args.date) argv.push('--date', args.date)
    if (args.supersedes) argv.push('--supersedes', args.supersedes)
    if (args.apply) argv.push('--apply')
    return { script: 'vault-write.py', argv, tempFiles: [{ content: args.body }] }
  }
  if (args.op === 'replace') {
    if (!args.target || args.newText === undefined) {
      return { error: 'replace requires target and newText' }
    }
    const argv = ['replace', '--target', args.target, '--source-file', TMPFILE_MARKER]
    if (args.expectedSha256) argv.push('--expected-sha256', args.expectedSha256)
    if (args.apply) argv.push('--apply')
    return { script: 'vault-write.py', argv, tempFiles: [{ content: args.newText }] }
  }
  // recover
  const argv = ['recover']
  if (args.apply) argv.push('--apply')
  return { script: 'vault-write.py', argv }
}

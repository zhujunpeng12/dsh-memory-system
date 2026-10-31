import { test, mock } from 'node:test'
import assert from 'node:assert/strict'
import childProcess from 'node:child_process'
import { syncBuiltinESMExports } from 'node:module'
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { apply } from './index.js'

// Exercise the real registered tool and runScript bridge, but force stream
// boundaries deterministically instead of relying on OS pipe chunk sizes.
for (const code of [0, 1]) {
  test(`Python output preserves split UTF-8 on stdout/stderr (exit ${code})`, async () => {
    const output = '中文记忆：修复完成🙂\n'
    const diagnostic = '错误详情：文件路径🔒\n'
    const spawn = mock.method(childProcess, 'spawn', () => {
      const child = new EventEmitter()
      child.stdout = new PassThrough()
      child.stderr = new PassThrough()
      child.kill = () => true
      setImmediate(() => {
        // Every multibyte character is split across multiple data events.
        for (const byte of Buffer.from(output)) child.stdout.write(Buffer.from([byte]))
        for (const byte of Buffer.from(diagnostic)) child.stderr.write(Buffer.from([byte]))
        child.stdout.end()
        child.stderr.end()
        setImmediate(() => child.emit('close', code))
      })
      return child
    })
    syncBuiltinESMExports()
    try {
      const definitions = []
      const agent = { ctx: {
        tools: { register(tool) { definitions.push(tool); return () => {} } },
        effect(factory) { return factory() },
      } }
      apply({
        agents: { roots: () => [agent] },
        on() { return () => {} },
        effect() { return () => {} },
      })
      const tool = definitions.find(item => item.name === 'memory_bootstrap')
      const result = await tool.execute({}, { agent, signal: new AbortController().signal })
      assert.equal(result.ok, code === 0)
      if (code === 0) {
        assert.equal(result.text, output.trim())
      } else {
        assert.equal(result.stdout, output.trim())
        assert.equal(result.stderr, diagnostic.trim())
      }
    } finally {
      spawn.mock.restore()
      syncBuiltinESMExports()
    }
  })
}

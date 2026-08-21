# Installation troubleshooting

This page covers plugin loading and first-run failures. It never asks you to upload memory files, credentials, or private paths.

## Supported baseline

| Component | Supported/tested baseline |
|---|---|
| DeepSeek Harness | `0.1.0-rc.7` |
| Node.js | 22 or 24 |
| Python | 3.10+ available as `python`, or set `PYTHON` |
| Storage | `~/.dsh-memory/` by default; optional `MEMORY_VAULT` override |

## Clean install or upgrade

Use the official profile-aware CLI so the package and bundle entry stay in sync:

```bash
npx @deepseek-ai/dsh plugin --profile web remove @zhujunpeng12/dsh-memory-system
npx @deepseek-ai/dsh plugin --profile web add github:zhujunpeng12/dsh-memory-system
```

Restart the `web` profile after install. Do not copy `index.js` or edit the generated `node_modules` directory by hand.

## `Cannot find package 'dsh-memory-system'`

Cause in 0.1.0-era GitHub installs: the bundle patch used an unscoped loader name even though the installed package is `@zhujunpeng12/dsh-memory-system`.

Fix: install 0.1.1 or later (or current GitHub `master`) and confirm `cordis.patch.yml` contains:

```yaml
name: '@zhujunpeng12/dsh-memory-system'
```

If the error persists, remove the package and reinstall with the commands above so a stale profile entry is not reused.

## `Cannot read properties of undefined` at `ctx.agents.roots()`

Cause in 0.1.0-era GitHub installs: the plugin used the `agents` service but declared only `tools` injection.

Fix: install 0.1.1 or later. The runtime export and both manifests now require `tools` and `agents`, and CI checks that they remain aligned.

## `Cannot find package '@deepseek-ai/dsh-tools'` or `@deepseek-ai/dsh-llm`

Cause in an early 0.1.1 development build: the plugin statically imported host packages. A profile can provide the `tools` service while Node's ESM resolver still cannot reach that package from a third-party plugin directory.

Fix: install the released 0.1.1 or later. The published plugin now registers native ToolDefinition objects through the injected service and has zero npm runtime dependencies. Do not install private copies of DSH internals into the profile as a workaround.

## Python cannot be started

The DSH host is JavaScript, while the memory engine uses Python standard-library scripts. Check:

```bash
python --version
```

If your executable has another name, start Harness with `PYTHON` pointing to it. Example in PowerShell:

```powershell
$env:PYTHON = "py"
```

When using the Windows `py` launcher, a wrapper executable may be preferable because the plugin invokes one executable followed by the script path.

## The tools load but `[vault-bootstrap]` is missing

1. Start a new session; automatic bootstrap is once per session.
2. Confirm `DSH_MEMORY_AUTO_INJECT` is not `false`.
3. Ask the agent to run `memory_gate` and `memory_bootstrap` explicitly.
4. Check that the configured vault is readable and contains `memory/` plus `projects/` (first run normally creates them).

Bootstrap failure is fail-open by design: memory enhancement must not prevent a normal DSH conversation.

## Reporting a bug

Use the repository bug-report form. Include DSH/Node/Python versions, operating system, install command, redacted error text, and whether a clean profile reproduces it. Never attach your vault, session logs, credentials, or absolute private paths.

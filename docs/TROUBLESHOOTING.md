# Installation troubleshooting

This page covers plugin loading and first-run failures. It never asks you to upload memory files, credentials, or private paths.

## Documented compatibility baseline

| Component | Documented baseline |
|---|---|
| DeepSeek Harness | `0.1.0-rc.7` |
| Node.js | 22 or 24 |
| Python | 3.10+ available as `python`, or set `PYTHON` |
| Storage | `~/.dsh-memory/` by default; optional `MEMORY_VAULT` override |

This is the documented compatibility target, not evidence that every listed combination was tested against a live DSH host. Local CLI demos, mocked plugin contract tests and package checks do not verify live session injection or approval dialogs. Verify installation and behavior in your target host environment.

The GitHub command below follows the default branch. Source-only demo and raw-lifecycle improvements described on a feature branch are not necessarily present there or in the published npm `0.1.0` package; inspect the checkout and its version notes. On 2026-11-01, the [npm registry](https://registry.npmjs.org/@zhujunpeng12%2Fdsh-memory-system) listed only `0.1.0`; the source version `0.1.1` is not evidence of an npm release.

## Clean install or upgrade

Use the official profile-aware CLI so the package and bundle entry stay in sync:

```bash
npx @deepseek-ai/dsh plugin --profile web remove @zhujunpeng12/dsh-memory-system
npx @deepseek-ai/dsh plugin --profile web add github:zhujunpeng12/dsh-memory-system
```

Restart the `web` profile after install. Do not copy `index.js` or edit the generated `node_modules` directory by hand.

## `Cannot find package 'dsh-memory-system'`

Cause in 0.1.0-era GitHub installs: the bundle patch used an unscoped loader name even though the installed package is `@zhujunpeng12/dsh-memory-system`.

Fix: use a source checkout containing the scoped-loader fix, install its locally packed tarball, and confirm `cordis.patch.yml` contains:

```yaml
name: '@zhujunpeng12/dsh-memory-system'
```

If the error persists, remove the package and reinstall with the commands above so a stale profile entry is not reused.

## `Cannot read properties of undefined` at `ctx.agents.roots()`

Cause in 0.1.0-era GitHub installs: the plugin used the `agents` service but declared only `tools` injection.

Fix: use a source checkout whose runtime export and both manifests require `tools` and `agents`, then install its locally packed tarball. The current source and CI contract checks keep these declarations aligned; do not assume the published npm `0.1.0` has this fix.

## `Cannot find package '@deepseek-ai/dsh-tools'` or `@deepseek-ai/dsh-llm`

Cause in an early 0.1.1 development build: the plugin statically imported host packages. A profile can provide the `tools` service while Node's ESM resolver still cannot reach that package from a third-party plugin directory.

Fix: use a source checkout containing the import-free registration fix and install its locally packed tarball. The current source registers native ToolDefinition objects through the injected service and declares no npm runtime dependencies. Do not install private copies of DSH internals into the profile as a workaround.

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

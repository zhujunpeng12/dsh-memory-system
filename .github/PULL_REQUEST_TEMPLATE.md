## Why

Describe the concrete problem and the evidence that it is real.

## What changed

- Describe the focused change.

## Safety boundaries

- [ ] No personal memory, credentials, usernames, or private absolute paths are included.
- [ ] Real vault data was not used for destructive, crash, or concurrency tests.
- [ ] Memory writes remain dry-run by default and approval-gated.
- [ ] Raw history remains append-only; corrections use `supersedes`.

## Verification

- [ ] `npm run check`
- [ ] `npm pack --dry-run`
- [ ] Install/loader behavior was tested when manifests or package metadata changed.

Paste concise test evidence here:

```text

```

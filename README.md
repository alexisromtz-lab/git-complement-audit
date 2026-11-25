# git-complement-audit

`git status` tells you what is in `HEAD`. It says nothing about the rest of the repository:
unmerged commits behind hidden refs, detached worktrees nobody adopted, files a reset
dropped, a `.env` that got committed once and then "removed". All of that still lives in the
object database — and `git worktree prune` / `git gc --prune` deletes it without a warning.

This CLI measures that complement, names every path in it, classifies each one, and tells you
whether anything in there is worth saving before Git throws it away.

Zero dependencies. Python 3.11+. Reads only; writes nothing except the files you ask for.

## Install

```bash
# one shot, nothing installed
uvx --from git+https://github.com/alexisromtz-lab/git-complement-audit git-complement-audit .

# or keep it around
pipx install git+https://github.com/alexisromtz-lab/git-complement-audit
pip install git+https://github.com/alexisromtz-lab/git-complement-audit
```

Not on PyPI yet. The package builds from source with `setuptools` and no runtime
dependencies, so `pip install .` inside a clone works too.

## Sample output

Run on the synthetic repository that `--self-test` builds (see [Verify the instrument](#verify-the-instrument)):

```
SCOPE     head=23831f083fa6 object-format=sha1 refs=2 worktrees=2 commits-outside-head=2
INCLUDED  all refs except HEAD; worktree HEADs; every path of each ref tip
UNIVERSE  paths-outside-head=6 blobs=6 bytes=112
CLASSES   MOVED=1 · ORPHAN_CODE=2 · ORPHAN_SECRET_RISK=1 · ORPHAN_DOC=1 · RESIDUE=1
  MOVED                     15 B  app_copy.py
  ORPHAN_CODE               27 B  orphan/handler.py
  ORPHAN_CODE               28 B  scratch/agent_notes.py
  ORPHAN_SECRET_RISK        22 B  .env
  ORPHAN_DOC                 9 B  docs/design.md
  RESIDUE                   11 B  build/out.bin
VERDICT   RC 1 — 3 finding(s) outside HEAD need a rescue or a commit (ORPHAN_CODE=2, ORPHAN_SECRET_RISK=1)
```

## Classes

Every path that is not in `HEAD` gets exactly one class. The order is the order of the list:
the first test that matches wins, so a secret is reported as a secret even if it also looks
like code, and a moved file is never reported as an orphan.

| class | meaning | fails the gate |
|---|---|---|
| `MOVED` | the exact same blob is already committed at another path in `HEAD` — content is safe, only the path was lost | no |
| `ORPHAN_SECRET_RISK` | `.env`, keys, credentials, or a blob whose first 64 KiB look like a token | **yes** |
| `ORPHAN_CODE` | source file that no commit in `HEAD` reaches | **yes** |
| `ORPHAN_DOC` | documentation or data outside `HEAD` | no |
| `RESIDUE` | build output, caches, archives: not worth rescuing | no |

## Exit codes

| code | meaning |
|---|---|
| `0` | nothing outside `HEAD` needs a rescue — no orphaned code, no secret risk |
| `1` | orphaned code or secret risk found (docs and residue alone do not fail) |
| `2` | not a repository, or invalid arguments — never a silent zero |

## Why two readings

A single `git rev-list --objects --all --not HEAD` cannot see a moved file: Git deduplicates
by object id, and if the blob is already reachable from `HEAD` the object is filtered out and
its path never appears. So `MOVED` would always read zero — a false green.

The tool therefore merges two readings: the full object walk (whole history, one path per
distinct blob) and one `ls-tree -r` per ref tip (every path, exactly). Every number in the
report comes from one of them, and the `INCLUDED` line says which ones ran.

## Flags

| flag | effect |
|---|---|
| `--json` | machine-readable report on stdout |
| `--rescue-dir PATH` | copy rescuable blobs outside the object database (see [Rescue](#rescue)) |
| `--receipt PATH` | write the deterministic pre-GC receipt (see [Receipt](#receipt-before-you-delete-anything)) |
| `--unreachable` | also scan objects no ref reaches — dropped stashes, dangling blobs |
| `--exclude-ref GLOB` | leave out refs matching a glob, repeatable, always declared in `scope` |
| `--ref-tips N` | list every path of each ref tip while tips ≤ N (default 200) |
| `--no-worktrees` | ignore detached worktree HEADs |
| `--quiet` | print nothing unless the contract breaks |
| `--self-test` | build the fixture and verify the instrument |

### Repositories driven by an agent runtime

Copilot, Claude Code and Cursor record a snapshot ref per turn (`refs/agents/…`). Those refs
dominate the complement: the audit reports the runtime's own snapshots as loudly as the work
you actually lost. Exclude them to see only what you can lose:

```bash
git-complement-audit . --exclude-ref 'refs/agents/*'
```

`scope` then carries `refs excluded`, so a smaller report never pretends to be a empty one.

## Declared scope

The report never claims more than it measured:

- `SCOPE` shows how many refs, worktrees and commits sit outside `HEAD` in this repository.
- `INCLUDED` lists the passes that actually ran. If more than 200 ref tips exist, the per-tip
  listing is skipped to keep the run bounded, and the report says so on a `NOTE` line —
  `MOVED` may then be under-reported. Raise the bound with `--ref-tips N`.
- `--unreachable` adds a `git fsck` pass for objects no ref reaches at all (dropped stashes,
  dangling blobs). It is off by default because it is the slowest pass.

## Rescue

```bash
git-complement-audit . --rescue-dir ~/rescue
```

Copies every rescuable blob (`ORPHAN_CODE`, `ORPHAN_SECRET_RISK`, `ORPHAN_DOC`) outside the
object database, re-hashing each one first: the file is only written if its bytes still hash
to the object id Git reported. The destination is created `0700`, every file inside is `0600`,
and the tree is laid out by class (`ORPHAN_CODE/…`, `ORPHAN_SECRET_RISK/…`) with a
`SHA256SUMS` and a `MANIFEST.json` so the rescue can be verified on its own, later, without
the repository.

## Receipt before you delete anything

```bash
git-complement-audit . --receipt pre-gc.json
git worktree prune && git gc --prune=now
```

The receipt is deterministic — same repository, same bytes, no timestamps — so it can be
committed or stored next to a backup and compared afterwards. It records the head, the
declared scope, the counts per class, and the SHA-256 of every blob that was outside `HEAD`.

## In CI

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }        # 0, or refs outside HEAD are invisible
- run: pipx run --spec git+https://github.com/alexisromtz-lab/git-complement-audit git-complement-audit .
```

`fetch-depth: 0` matters: a shallow clone has no complement, so the audit would pass by
measuring nothing.

As a `pre-commit` hook, add to `.pre-commit-config.yaml`:

```yaml
- repo: https://github.com/alexisromtz-lab/git-complement-audit
  rev: v1.0.0
  hooks:
    - id: git-complement-audit
```

## Verify the instrument

```bash
git-complement-audit --self-test
```

Builds a throwaway repository in a temporary directory — two refs, a detached worktree, one
file per class, one dangling secret — audits it, rescues it, and checks the result:

```
SELF-TEST passed: 31 checks on a synthetic repository
fixture classes: MOVED=1 · ORPHAN_CODE=2 · ORPHAN_SECRET_RISK=1 · ORPHAN_DOC=1 · RESIDUE=1
fixture verdict: RC 1 · rescue 4 files re-hashed · receipt byte-identical · clean RC 0 · invalid RC 2
```

The fixture uses fixed commit dates, so its `HEAD` is `23831f083fa6…` on any machine. If the
numbers above stop matching, the engine changed — not the environment.

`python -m unittest discover -s tests` adds the boundary contract: exit codes, receipt
determinism between two runs, and the `0700`/`0600` permissions of a rescue.

## Limits, stated

- It audits one repository at a time; the object database is the unit, not the filesystem.
- `--unreachable` finds objects no ref reaches, but a blob that was never written cannot be found.
- Secret detection is path- and prefix-based on the first 64 KiB. It flags risk; it does not
  prove a credential is live.
- Nothing leaves the machine: no network, no telemetry.

## License

MIT.

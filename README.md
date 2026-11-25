# git-complement-audit

`git status` tells you what is in `HEAD`. It says nothing about the rest of the repository:
unmerged commits behind hidden refs, detached worktrees nobody adopted, files a reset
dropped, a `.env` that got committed once and then "removed". All of that still lives in the
object database — and `git worktree prune` / `git gc --prune` deletes it without a warning.
The credential you deleted is worse: it survives every `git gc` and travels in every clone,
because it is reachable from the history, not from `HEAD`.

This CLI measures that complement, names every path in it, classifies each one, and tells you
whether anything in there is worth saving before Git throws it away. It also counts — and on
request reads — the blobs that no path outside `HEAD` can name, so a clean report never
covers bytes the instrument did not look at.

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
SCOPE     head=23831f083fa6 object-format=sha1 refs=3 worktrees=2 commits-outside-head=3
INCLUDED  all refs except HEAD; worktree HEADs; every path of each ref tip
HIDDEN    runtime namespaces: refs/agents/1
UNIVERSE  paths-outside-head=8 blobs=8 bytes=167
HISTORY   blobs-outside-head=8 named=7 unnamed=1 scan=off
CLASSES   MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0
NOTE      1 blob(s) outside HEAD are named by no path row — their path is still in HEAD, or another blob took it — so their contents were not read; a credential there survives in every clone; --scan-history reads them
  MOVED                     15 B  app_copy.py
  ORPHAN_CODE               27 B  orphan/handler.py
  ORPHAN_CODE               31 B  runtime/checkpoints/lost.py
  ORPHAN_CODE               28 B  scratch/agent_notes.py
  ORPHAN_SECRET_RISK        22 B  .env
  ORPHAN_SECRET_RISK        24 B  runtime/checkpoints/.env
  ORPHAN_DOC                 9 B  docs/design.md
  RESIDUE                   11 B  build/out.bin
VERDICT   RC 1 — 5 finding(s) outside HEAD need a rescue, a commit or a rotation (ORPHAN_CODE=3, ORPHAN_SECRET_RISK=2)
```

Add `--scan-history` and the one blob no path row could name is read too:

```
HISTORY   blobs-outside-head=8 named=7 unnamed=1 scan=on
  HISTORY_SECRET_RISK        28 B  README.md  [history]
VERDICT   RC 1 — 6 finding(s) outside HEAD need a rescue, a commit or a rotation (ORPHAN_CODE=3, ORPHAN_SECRET_RISK=2, HISTORY_SECRET_RISK=1)
```

## Classes

Every path that is not in `HEAD` gets exactly one class. The order is the order of the list:
the first test that matches wins, so a secret is reported as a secret even if it also looks
like code, and a moved file is never reported as an orphan.

| class | meaning | fails the gate | rescuable |
|---|---|---|---|
| `MOVED` | the exact same blob is already committed at another path in `HEAD` — content is safe, only the path was lost | no | no |
| `ORPHAN_SECRET_RISK` | `.env`, keys, credentials, or a blob whose first 64 KiB look like a token | **yes** | yes |
| `ORPHAN_CODE` | source file that no commit in `HEAD` reaches | **yes** | yes |
| `ORPHAN_DOC` | documentation or data outside `HEAD` | no | yes |
| `RESIDUE` | build output, caches, archives: not worth rescuing | no | no |
| `HISTORY_SECRET_RISK` | a blob outside `HEAD` that no path row can name and whose first 64 KiB look like a token — reported only under `--scan-history` | **yes** | **no: rotate it** |

The first five are decided by the path; the last one is decided by the content, because its
path is not evidence of anything: it still exists in `HEAD`, holding a different blob. A
credential in that state is not something to copy to a rescue directory and keep — it is
something to revoke, which is why it is the one failing class the rescue refuses to write.

## Exit codes

| code | meaning |
|---|---|
| `0` | nothing outside `HEAD` needs a rescue — no orphaned code, no secret risk, no history credential |
| `1` | orphaned code, secret risk or history credential found (docs and residue alone do not fail) |
| `2` | not a repository, or invalid arguments — never a silent zero |

## Why two readings

A single `git rev-list --objects --all --not HEAD` cannot see a moved file: Git deduplicates
by object id, and if the blob is already reachable from `HEAD` the object is filtered out and
its path never appears. So `MOVED` would always read zero — a false green.

The tool therefore merges two readings: the full object walk (whole history, one path per
distinct blob) and one `ls-tree -r` per ref tip (every path, exactly). Every number in the
report comes from one of them, and the `INCLUDED` line says which ones ran.

## The blob that no path can name

The complement splits in two, and the split is a partition, so the report publishes both
addends instead of only their sum:

```
blobs_outside_head = blobs_named + blobs_not_named
```

A *named* blob is one a row of the report points at: its path is not in `HEAD`, so losing the
ref would lose the path. An *unnamed* blob has no such row — either every path that reaches
it also exists in `HEAD` (holding a different, newer blob), or a second blob took its path in
the by-path index. That is the shape of a secret you deleted:

```bash
git rm --cached .env && git commit -m "remove the key"   # .env still exists in HEAD's tree
```

`HEAD` is clean, `git status` is clean, no path outside `HEAD` exists, and the old blob is
still reachable from the parent commit — so it survives `git gc` and ships in every clone.
The default run does not read those blobs, and says so on a `NOTE` line. `--scan-history`
reads them — one `git cat-file --batch` over the unnamed set, capped at 64 KiB per blob — and
a credential among them is `HISTORY_SECRET_RISK`, which fails the gate.

```bash
git-complement-audit . --scan-history
```

It is not the default because it reads content the audit otherwise only classifies by name,
and on a large object database that is the expensive pass. It is not silent either: `scan=off`
with `unnamed>0` always prints what was left unread.

## Flags

| flag | effect |
|---|---|
| `--json` | machine-readable report on stdout |
| `--rescue-dir PATH` | copy rescuable blobs outside the object database (see [Rescue](#rescue)) |
| `--receipt PATH` | write the deterministic pre-GC receipt (see [Receipt](#receipt-before-you-delete-anything)) |
| `--unreachable` | also scan objects no ref reaches — dropped stashes, dangling blobs |
| `--scan-history` | read the blobs outside `HEAD` that no path row can name, and fail on a credential among them — see [below](#the-blob-that-no-path-can-name) |
| `--exclude-ref GLOB` | leave out refs matching a glob, repeatable; noise dropped, secrets kept — see [below](#repositories-driven-by-an-agent-runtime) |
| `--allow-excluded-secrets` | with `--exclude-ref`, also drop secrets from those refs (declared in `scope`) |
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

Excluding a ref removes its **noise** — `MOVED`, `ORPHAN_CODE`, `ORPHAN_DOC`, `RESIDUE` — but
not its **secrets**. A commit an agent made by accident is exactly where a committed `.env`
ends up, so the pass keeps reporting `ORPHAN_SECRET_RISK` from the refs you excluded and
labels each such finding `[excluded refs]`:

```
EXCLUDED  patterns=refs/agents/* refs=1 tips=1 secret-scan=on
HIDDEN    runtime namespaces: refs/agents/1
HISTORY   blobs-outside-head=8 named=7 unnamed=1 scan=off
CLASSES   MOVED=1 · ORPHAN_CODE=2 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0
  MOVED                     15 B  app_copy.py
  ORPHAN_CODE               27 B  orphan/handler.py
  ORPHAN_CODE               28 B  scratch/agent_notes.py
  ORPHAN_SECRET_RISK        22 B  .env
  ORPHAN_SECRET_RISK        24 B  runtime/checkpoints/.env  [excluded refs]
  ORPHAN_DOC                 9 B  docs/design.md
  RESIDUE                   11 B  build/out.bin
```

The excluded ref's own paths are still named, so they do not migrate into the `unnamed`
count: `blobs_not_named` stays `1` with and without `--exclude-ref`. Excluding a namespace
removes rows from the report; it never turns a named blob into an unread one.

`RC` stays `1` while a secret sits inside an excluded namespace. If you want the old
behaviour — everything inside the excluded refs ignored — ask for it explicitly:

```bash
git-complement-audit . --exclude-ref 'refs/agents/*' --allow-excluded-secrets
```

`scope` then reads `excluded secret scan: off (--allow-excluded-secrets)` and a `NOTE` line
counts the tips that were not scanned. Silence is always declared; it is never inferred.

`HIDDEN` counts the runtime namespaces that exist in the object database
(`refs/agents/`, `refs/checkpoints/`, `refs/original/`) even when they are excluded, and
`--json` exposes the same census as `hidden_refs`, so a clean `HEAD` and a clean `origin`
never pass for an empty object database.

## Declared scope

The report never claims more than it measured:

- `SCOPE` shows how many refs, worktrees and commits sit outside `HEAD` in this repository.
- `INCLUDED` lists the passes that actually ran. If more than 200 ref tips exist, the per-tip
  listing is skipped to keep the run bounded, and the report says so on a `NOTE` line —
  `MOVED` may then be under-reported. Raise the bound with `--ref-tips N`.
- `EXCLUDED` appears only when `--exclude-ref` was used, and restates all of it: the patterns,
  how many refs matched, how many tips were held out, and whether their secrets were scanned.
  A finding whose origin is an excluded ref carries `root: "excluded refs"` in `--json` and the
  `[excluded refs]` suffix in the text report; every other finding carries `root: "refs"`.
- `HIDDEN` counts the runtime namespaces present in the object database, excluded or not, so
  "nothing to report" and "nothing to look at" stay two different statements.
- `HISTORY` states the partition of the blob complement: how many blobs are outside `HEAD`,
  how many a path row names, how many it cannot, and whether their contents were read. A run
  that reports `unnamed>0` has not looked at those bytes; `--scan-history` does.
- `--allow-excluded-secrets` narrows the universe by request: blobs that live only in the
  excluded refs leave both addends, and the `EXCLUDED` line records `secret-scan=off`. The
  count does not shrink silently — it shrinks on a line that says so.
- The secret scan over excluded refs walks the held-out tips directly, without `--not`: `--not`
  would drop every blob shared with an included root, and a secret shared with one would
  vanish. The pass prints one path per **distinct blob**, so two secret-named paths that hold
  identical bytes are reported once, under one of the two. It never hides a secret; it may
  name it under its twin path.
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

`HISTORY_SECRET_RISK` is never rescued. A path that no row names yields no path to write, and
the artefact is a live-looking credential: the remedy is `git filter-repo` and a rotation, not
a copy in a safe directory.

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
  rev: v1.3.0
  hooks:
    - id: git-complement-audit
```

## Verify the instrument

```bash
git-complement-audit --self-test
```

Builds a throwaway repository in a temporary directory — three refs, a detached worktree, one
file per class, a secret inside a runtime ref, a secret at a path that still exists in `HEAD`,
one dangling secret — audits it every way the flags allow, rescues it, and checks the result:

```
SELF-TEST passed: 51 checks on a synthetic repository
fixture classes: MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0
fixture verdict: RC 1 · rescue 6 files re-hashed · receipt byte-identical · clean RC 0 · invalid RC 2
```

The fixture uses fixed commit dates, so its `HEAD` is `23831f083fa6…` on any machine. If the
numbers above stop matching, the engine changed — not the environment.

`python -m unittest discover -s tests` adds the boundary contract: exit codes, receipt
determinism between two runs, the `0700`/`0600` permissions of a rescue, and the fact that a
credential at a path that still exists in `HEAD` is declared in the universe before it is read.

## Limits, stated

- It audits one repository at a time; the object database is the unit, not the filesystem.
- `--unreachable` finds objects no ref reaches, but a blob that was never written cannot be found.
- Secret detection is path- and prefix-based on the first 64 KiB. It flags risk; it does not
  prove a credential is live.
- Without `--scan-history` the unnamed blobs are counted but not read. That is a declared
  skip, printed on every run where it happens; it is not a zero.
- A secret at a path that *is* outside `HEAD` but does not look like one (`config.py` with a
  key inside) is classified `ORPHAN_CODE` by its path. `--scan-history` does not cover it,
  because its path is a name the report can publish.
- Nothing leaves the machine: no network, no telemetry.

## License

MIT.

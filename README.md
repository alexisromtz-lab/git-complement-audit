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
CLOSURE   odb-blobs=11 = head=3 + complement=8 + uncovered=0
CLASSES   MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0 · EXEMPT_SECRET=0
STRATA    as-of=1767225600 (HEAD committer date) live<=30d=1 · recent31-90d=2 · stale>90d=5 · history-only=0 of 8 ref row(s)
REFS      live<=30d=1 · recent31-90d=1 · stale>90d=1 of 3 ref(s)
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
CLOSURE   odb-blobs=11 = head=3 + complement=8 + uncovered=0
CLASSES   MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=1 · EXEMPT_SECRET=0
  HISTORY_SECRET_RISK        28 B  README.md  [history]
VERDICT   RC 1 — 6 finding(s) outside HEAD need a rescue, a commit or a rotation (ORPHAN_CODE=3, ORPHAN_SECRET_RISK=2, HISTORY_SECRET_RISK=1)
```

Declare those two blob ids in `.git-complement-audit-ignore` and the same repository reads
the other way — nothing is lost, and the claim is still on the page:

```
EXEMPT    .git-complement-audit-ignore declared=2 applied=2 unused=0
CLOSURE   odb-blobs=11 = head=3 + complement=8 + uncovered=0
CLASSES   MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=1 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0 · EXEMPT_SECRET=2
  EXEMPT_SECRET             22 B  .env  [ORPHAN_SECRET_RISK → declared at .git-complement-audit-ignore:2]
  EXEMPT_SECRET             28 B  README.md  [HISTORY_SECRET_RISK → declared at .git-complement-audit-ignore:1]
VERDICT   RC 1 — 4 finding(s) outside HEAD need a rescue, a commit or a rotation (ORPHAN_CODE=3, ORPHAN_SECRET_RISK=1)
```

## Classes

Every path that is not in `HEAD` gets exactly one class. The order is the order of the list:
the first test that matches wins, so a secret is reported as a secret even if it also looks
like code, and a moved file is never reported as an orphan. The last row is the exception: a
declaration is applied after the other six have spoken, and it can only replace a secret
claim, never a lost path.

| class | meaning | fails the gate | rescuable |
|---|---|---|---|
| `MOVED` | the exact same blob is already committed at another path in `HEAD` — content is safe, only the path was lost | no | no |
| `ORPHAN_SECRET_RISK` | `.env`, keys, credentials, or a blob whose first 64 KiB look like a token | **yes** | yes |
| `ORPHAN_CODE` | source file that no commit in `HEAD` reaches | **yes** | yes |
| `ORPHAN_DOC` | documentation or data outside `HEAD` | no | yes |
| `RESIDUE` | build output, caches, archives: not worth rescuing | no | no |
| `HISTORY_SECRET_RISK` | a blob outside `HEAD` that no path row can name and whose first 64 KiB look like a token — reported only under `--scan-history` | **yes** | **no: rotate it** |
| `EXEMPT_SECRET` | a claim of danger whose exact blob id is declared in `.git-complement-audit-ignore`, with the class it replaced — see [Exemptions](#exemptions-by-content-address) | no | no |

The first six are decided by the path, or by the content where the path is not evidence; the
last one is decided by a declaration and only ever replaces the two secret classes. A
credential in `HISTORY_SECRET_RISK` is not something to copy to a rescue directory and keep —
it is something to revoke, which is why it is a failing class the rescue refuses to write.

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

## The database closes

Every number above is a reading taken from refs. A report built that way is only as complete
as its population, so the last line of the measurement is an identity instead of a count:

```
odb_blobs = head_blobs + covered_blobs + uncovered_blobs
```

`odb_blobs` comes from `git cat-file --batch-all-objects --batch-check`, which asks the object
database what it holds rather than asking a ref what it reaches. `head_blobs` is what `HEAD`
reaches. `covered_blobs` is what this report has a row or a blob for. The remainder is the
population no ref reaches — the objects `git gc --prune` deletes without ever having named
them, which no `rev-list --all` can show you:

```bash
git hash-object -w --stdin <<< 'AWS_SECRET_ACCESS_KEY=...'   # no ref, no path, no warning
git gc --prune=now                                          # gone
```

When `uncovered` is not zero the render prints `NOTE` and names the flag that reads them;
`--unreachable` walks `git fsck --unreachable` and returns them as rows, so the second run
closes to zero. A gap is never an exit code of its own — the verdict still comes from the six
classes — but a report that does not say how much of the database it left out would be the
false green this tool exists to prevent.

## Exemptions, by content address

An audit that cannot be run on its own repository is an opinion, not an instrument. The
commonest way that happens is a detector whose own source spells out the tokens it looks for:
every historical copy of the file then matches, nothing can clean the past without rewriting
published objects, and the gate stays red forever. `.git-complement-audit-ignore` is the way
out, and it is deliberately narrow:

```gitignore
# one blob id per line, with an optional reason after '#'
0770ac7bf8ebb5e67edce2fea58110217d6374ac  # its bytes named this tool's dictionary, not a credential
```

- **By object id, never by name.** An exemption costs a content address; it cannot be handed
  to a directory, a glob or a file name, and it cannot outlive the bytes it names.
- **Only a claim of danger.** `ORPHAN_SECRET_RISK` and `HISTORY_SECRET_RISK` are the only
  eligible classes. A blob missing from `HEAD` is evidence of loss, and no declaration in the
  audited repository may quiet it.
- **Always declared.** Every run prints `EXEMPT … declared=N applied=N unused=N`, and each
  exempted row keeps the class it replaced. A declaration that never fires is named on a
  `NOTE` line instead of being quietly ignored, and a malformed line stops the run with RC 2.
- **Nothing is written.** Withdrawing the file restores the finding, byte for byte; the fixture
  checks exactly that.

The tool audits itself this way: nine historical copies of `engine.py`, `selfcheck.py` and
`tests/test_audit.py`, from before the detector learned to assemble its markers at run time,
are declared in [`.git-complement-audit-ignore`](.git-complement-audit-ignore). Six were
already outside `HEAD`; the other three were the published v1.3.0 versions, which became
history the moment v1.3.1 entered it — a cleanup publishes its own predecessor. `--scan-history`
on this repository exits 0 with `EXEMPT_SECRET=9`, `unused=0`, and every blob created from here
on is clean by construction.

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
| `--ignore-file PATH` | exemption file of declared blob ids (default: `.git-complement-audit-ignore` at the repository root, if present) — see [Exemptions](#exemptions-by-content-address) |
| `--ref-tips N` | list every path of each ref tip while tips ≤ N (default 200) |
| `--as-of DATE` | date the age strata against `DATE` — unix seconds, `YYYY-MM-DD` or ISO 8601; default: the committer date of `HEAD` (see [Age strata](#age-strata)) |
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
CLOSURE   odb-blobs=11 = head=3 + complement=8 + uncovered=0
CLASSES   MOVED=1 · ORPHAN_CODE=2 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0 · EXEMPT_SECRET=0
STRATA    as-of=1767225600 (HEAD committer date) live<=30d=1 · recent31-90d=0 · stale>90d=5 · history-only=0 of 6 ref row(s)
REFS      live<=30d=1 · recent31-90d=0 · stale>90d=1 of 2 ref(s)
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
- `STRATA` dates the ref rows against one declared reference and states the partition; when
  the per-tip listing was skipped it reads `NO_MEDIDO` instead of a zero — see
  [Age strata](#age-strata).
- `REFS` publishes the ref census that `SCOPE` counts: every ref the run kept, in the same three
  windows. It is a partition of the refs, not of the rows, so it shrinks by exactly the refs
  `--exclude-ref` named.

## Age strata

A raw count of the complement mixes two populations a reviewer treats differently: work that
moved last week on a branch nobody merged, and a ref left behind in 2021. `STRATA` partitions
the rows outside `HEAD` by the age of the newest ref tip that still holds them:

```
STRATA    as-of=1767225600 (HEAD committer date) live<=30d=1 · recent31-90d=2 · stale>90d=5 · history-only=0 of 8 ref row(s)
REFS      live<=30d=1 · recent31-90d=1 · stale>90d=1 of 3 ref(s)
```

- **The reference is a commit date, not the clock.** By default it is the committer date of
  `HEAD`, so two runs over the same tree agree byte for byte on any machine on any day;
  `--as-of` dates a historical snapshot instead. A tip newer than the reference is counted
  live and declared on a `NOTE` line — skew is a fact about the reference, never a fourth
  bucket and never a silent negative age.
- **It dates presence, not freshness.** A row's age is that of the newest tip whose tree holds
  *that path with that content*. A file untouched for years inside a branch moved yesterday is
  live work; a path whose content already sits in `HEAD` under another name is still dated by
  the branch that lost it, which is why `MOVED` rows do not quietly become live.
- **The buckets are a partition, and it is published.** `live + recent + stale + history_only`
  equals `rows`, where `rows` is exactly the findings whose `root` is `refs`. Rows reached only
  by an excluded ref, by `--scan-history` or by `--unreachable` carry a different `root` and are
  not dated here: the strata measure the complement, not the run.
- **`history-only` is named, not folded.** A row that no tip tree holds survives only in commits
  the branches have moved past: no branch carries it any more. Calling that `stale` would
  describe an old branch that does not exist.
- **No tip tree, no strata.** When more than `--ref-tips N` tips exist the per-tip listing is
  skipped, and the block reads `state: "NO_MEDIDO"` with the reason. An empty bucket is a
  measurement; a missing measurement is not an empty bucket.
- **`REFS` answers the question `STRATA` cannot.** The rows say how much of the complement each
  window holds; the census says how many refs moved in each window, whatever their paths are. The
  three-bucket identity `live + recent + stale == total` is exact there, because every ref carries
  one tip date. Worktree HEADs date rows but are not refs, so they stay out of the census: it adds
  up against the `refs=` count on the `SCOPE` line, minus the refs `--exclude-ref` left out.
- **`--as-of` changes no verdict.** It moves buckets only: exit codes, findings and the receipt
  of the same tree stay identical, and the block is signed into the receipt like the rest.

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
  rev: v1.5.0
  hooks:
    - id: git-complement-audit
```

## Citing this

`CITATION.cff` and `.zenodo.json` travel in the repository and inside the built
distribution, and both name the version they describe. Cite the version you ran, not the
idea: a report produced by 1.5.0 and a report produced by an older tag are different
instruments with the same name.

The 1.5.0 deposit is archived at Zenodo as `10.5281/zenodo.24575076`; `CITATION.cff`
carries the same identifier, and `tests/test_audit.py` fails if the two surfaces disagree.

## Verify the instrument

```bash
git-complement-audit --self-test
```

Builds a throwaway repository in a temporary directory — three refs, one stratum apart, a
detached worktree, one file per class, a secret inside a runtime ref, a secret at a path that
still exists in `HEAD`, one dangling secret — audits it every way the flags allow, declares an
exemption and withdraws it, rescues it, and checks the result:

```
SELF-TEST passed: 89 checks on a synthetic repository
fixture classes: MOVED=1 · ORPHAN_CODE=3 · ORPHAN_SECRET_RISK=2 · ORPHAN_DOC=1 · RESIDUE=1 · HISTORY_SECRET_RISK=0 · EXEMPT_SECRET=0
fixture closure: odb-blobs=12 = head=3 + complement=9 + uncovered=0
fixture strata: live<=30d=1 · recent31-90d=2 · stale>90d=5 · history-only=0 of 8 ref rows, as-of HEAD 1767225600
fixture refs: live<=30d=1 · recent31-90d=1 · stale>90d=1 of 3 ref(s)
fixture verdict: RC 1 · rescue 6 files re-hashed · receipt byte-identical · clean RC 0 · invalid RC 2
```

The fixture uses fixed commit dates, so its `HEAD` is `23831f083fa6…` on any machine, and its
three refs land one per age stratum — live, recent and stale — on every run. If the
numbers above stop matching, the engine changed — not the environment.

`python -m unittest discover -s tests` adds the boundary contract: exit codes, receipt
determinism between two runs, the `0700`/`0600` permissions of a rescue, the fact that a
credential at a path that still exists in `HEAD` is declared in the universe before it is read,
and that a declared exemption moves a secret out of the gate while leaving orphan code failing.

## Limits, stated

- It audits one repository at a time; the object database is the unit, not the filesystem.
- The closure reads the whole object database once, with `git cat-file --batch-all-objects`.
  On a small repository that is milliseconds; on a database of millions of objects it is the
  one line that dominates the run, and it is worth it, because without it the report cannot
  say how much of the database it did not look at. If the identity does not close, the gap is
  printed, and `--unreachable` is what closes it.
- `--unreachable` finds objects no ref reaches, but a blob that was never written cannot be found.
- Secret detection is path-based, plus a token *shape* — a known prefix followed by a body of
  a known length — over the first 64 KiB. It flags risk; it does not prove a credential is live,
  and a token split across lines or stored without its prefix is not seen.
- An exemption reduces the gate only for the blob id it names, and the run always says how many
  it spent and how many never fired. It is a record of a decision, not a way to switch the
  detector off: the two classes it can touch are the secret ones, and even then the row stays
  on the page with the class it replaced.
- Without `--scan-history` the unnamed blobs are counted but not read. That is a declared
  skip, printed on every run where it happens; it is not a zero.
- The age strata date a row by the tip trees, so they need the per-tip listing: past
  `--ref-tips N` tips the block reads `NO_MEDIDO` rather than dating a row by a walk that
  cannot say which branch holds it.
- A secret at a path that *is* outside `HEAD` but does not look like one (`config.py` with a
  key inside) is classified `ORPHAN_CODE` by its path. `--scan-history` does not cover it,
  because its path is a name the report can publish.
- Nothing leaves the machine: no network, no telemetry.

## License

MIT.

## Field notes

This repository belongs to an audit corpus whose measurements are published as
field notes. Two of them come from the same work:

- [El bug que responde SUCCESS: auditoría reproducible de litellm#46923](https://dev.to/alexisromtz7/el-bug-que-responde-success-auditoria-reproducible-de-litellm46923-ad6)
- [The bug that answers SUCCESS: a reproducible audit of litellm#46923](https://dev.to/alexisromtz7/the-bug-that-answers-success-a-reproducible-audit-of-litellm46923-15lk)

The instrument has also been run against repositories it does not own, with the prediction
written before the first clone: [EXTERNAL_BASELINE.md](EXTERNAL_BASELINE.md). It is the only
place in this repository where a number does not come from `--self-test`, and it declares why.

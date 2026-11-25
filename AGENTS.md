# AGENTS.md — working in this repository

Rules for any agent or human changing this code. Short, and each one is checked by the
self-test or the contract tests.

## What this repository is

A single-purpose CLI: measure what Git keeps outside `HEAD`, classify it, say whether it is
worth saving before `git gc --prune` destroys it. Nothing else. No daemon, no server, no
network client, no plugin system.

## Invariants (breaking one is a bug, not a tradeoff)

1. **Zero third-party dependencies.** Standard library only. `pyproject.toml` has
   `dependencies = []` and it stays that way.
2. **The audit reads.** `audit()` writes no file and mutates no ref. The only writes in the
   program are `--rescue-dir` and `--receipt`, both opt-in by the caller.
3. **No network.** No sockets, no telemetry, no update check, no download.
4. **Never touch the audited repository.** No `git gc`, no `git worktree prune`, no `update-ref`,
   no `checkout` against the subject. The tool reports; the operator deletes.
5. **Exit codes are a contract**: `0` clean, `1` orphaned code or secret risk, `2` invalid
   repository or arguments. Never invent a fourth code, never return `0` when a pass did not run.
6. **A declared scope, not a silent one.** If a pass is skipped (bounded ref-tip listing,
   `--unreachable` off), the report says so — `scope` in the JSON, `INCLUDED`/`NOTE` in the
   render. An undeclared skip is a false green.
7. **Numbers in `README.md` come from `--self-test` only.** The fixture has fixed commit dates,
   so its head is reproducible. If the README shows a count, the self-test prints the same one.
   Never quote a measurement taken from a private repository.
8. **No absolute paths in the report.** `repo` carries a head, an object format and counts;
   never a home-directory path, never a hostname.
9. **`--exclude-ref` removes noise, never evidence.** Excluding a ref may drop `MOVED`,
   `ORPHAN_CODE`, `ORPHAN_DOC` and `RESIDUE` from it; `ORPHAN_SECRET_RISK` survives and is
   labelled with its origin, because a ref an agent wrote by accident is exactly where a
   committed `.env` hides. Dropping those too requires `--allow-excluded-secrets`, and the
   report must say the scan was off. A smaller report may never be a quieter one.
10. **An exemption is a record, not a switch.** `.git-complement-audit-ignore` grants a class
    change to a blob id — never to a name, a glob or a directory — and only for
    `ORPHAN_SECRET_RISK` and `HISTORY_SECRET_RISK`. It is applied after the six classes have
    spoken, the replaced class is kept on the row, and every run prints `declared`, `applied`
    and `unused`. The file is read, never written: withdrawing it restores the finding. A
    detector whose own vocabulary trips it must fix the predicate and, for the blobs already
    published, declare them here in the same commit.

## Verification, before any commit

```bash
python -m git_complement_audit --self-test          # 67 in-process checks, RC 0
python -m unittest discover -s tests                # CLI contract, RC 0
```

Both must pass. A change to the engine that does not change the fixture numbers is suspect:
state why in the commit message.

## Changing the classifier

Classes are mutually exclusive and total. `engine.CLASSES` fixes the order, and the first test
that matches wins: `MOVED` → `ORPHAN_SECRET_RISK` → `ORPHAN_CODE` → `ORPHAN_DOC` → `RESIDUE`,
then `HISTORY_SECRET_RISK`, which is decided by content after the path loop and therefore
cannot be reached by a path at all, and last `EXEMPT_SECRET`, which is not a test at all: it is
the declaration of `.git-complement-audit-ignore`, applied to the two secret classes by blob
id. A new class needs, in the same commit: the constant, the order, the `FAILING` decision, the
README table, and a fixture file in `selfcheck.py` that lands in it.

## Publishing the universe

The blob complement is a partition, `blobs_outside_head = blobs_named + blobs_not_named`, and
both addends travel in every report. A blob is *named* when a row points at it — including a
row the report holds back because its ref was excluded. It is *unnamed* when no path outside
`HEAD` reaches it, which is the shape of a credential deleted but still in the history: those
bytes are counted always, read only under `--scan-history`, and the skip is printed. Never
derive one addend from the other, and never let a flag move a blob from one to the other
without saying so on a line of the report.

## Style

- English in code, comments, commit messages and docs.
- A comment explains *why*, never *what*. No banner comments.
- Every number printed by the tool is either measured in the same run or absent. No estimates.
- Keep the surface small: a flag that is not proven by a test does not ship.

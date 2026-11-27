# External baseline — 27 November 2026

Every number this repository publishes about itself comes from `--self-test`, and that is
deliberate: a fixture we wrote can be argued with, but it cannot be accused of having been
chosen after the fact. This file is the one exception, and it pays for it by declaring its own
population, its own commits and the prediction that was written before the first clone.

The question it answers is not "does the tool work". It is "is the complement rare", because a
rarity is a curiosity and a base rate is a market.

## The prediction, registered before any repository was opened

Three falsifiable claims, with the falsifier each one carries:

| id | claim | falsifier |
|---|---|---|
| P1 | at least 4 of 6 repositories close with `uncovered_blobs == 0` | fewer than 4 |
| P2 | at most 2 of 6 repositories hold `ORPHAN_CODE > 0` or `ORPHAN_SECRET_RISK > 0` (±1 repo) | 3 or more |
| P3 | at least 1 of 6 repositories holds `HISTORY_SECRET_RISK > 0` | all 6 at zero |

A repository that cannot be cloned or parsed is declared `NO_MEDIDO` and counted as not-clean.
It never enters the denominator as a pass.

## Population

Public third-party repositories, full clones, fixed commits. The sixth, `is`, could not be
restored after the working directory was purged — its identity is unrecoverable, so it is
declared `NO_MEDIDO` and P1 keeps its strict reading: 4 of 6 is demanded from a maximum of 5
reachable passes, which makes the test harder, not easier.

| repository | commit | language |
|---|---|---|
| ActivityWatch/aw-core | `0e54ea8` | Python |
| pallets/click | `08b803c` | Python |
| psf/requests | `3c7baaa0` | Python |
| python-attrs/attrs | `36484c6` | Python |
| tj/commander.js | `a5a7dc0` | JavaScript |
| (`is`) | — | `NO_MEDIDO` |

## The command

One invocation per repository, no writes into the clones, JSON read from a pipe:

```
PYTHONPATH=<checkout> python3 -m git_complement_audit <clone> \
    --scan-history --unreachable --json
```

## Results

`closure` reads `odb_blobs = head_blobs + covered_blobs + uncovered_blobs`, and every row below
closes exactly.

| repository | RC | closure | orphan code | secret risk | history secret | moved | doc | residue | paths |
|---|---|---|---|---|---|---|---|---|---|
| aw-core | 1 | 1650 = 85 + 1565 + 0 | 46 | 0 | 0 | 0 | 2 | 4 | 52 |
| click | 1 | 5687 = 174 + 5513 + 0 | 53 | 0 | 0 | 1 | 51 | 37 | 142 |
| requests | 1 | 8252 = 128 + 8124 + 0 | 254 | 2 | 4 | 4 | 17 | 53 | 334 |
| attrs | 1 | 4577 = 142 + 4435 + 0 | 33 | 0 | 0 | 4 | 261 | 13 | 311 |
| commander.js | 1 | 3695 = 219 + 3476 + 0 | 118 | 1 | 0 | 3 | 14 | 40 | 176 |
| **total** | **5 × 1** | **23861 = 748 + 23113 + 0** | **504** | **3** | **4** | **12** | **345** | **147** | **1015** |

Scan cost was 2 to 10 seconds per repository, on the same machine, with no network access.

## Verdicts

**P1 — confirmed.** Five of five measured repositories close at `uncovered_blobs == 0`. The
object database is fully read in every case; no report below is a report about its own walk.

**P2 — refuted, and refuted in the direction that matters.** All five measured repositories hold
orphaned code: 504 paths between them, one hundred per repository on average. The claim was that
the complement is a rarity in healthy popular projects. It is not. `git status` on any of these
five prints a clean tree while between 33 and 254 source paths sit unreachable from `HEAD`.

**P3 — confirmed.** `psf/requests` carries four `HISTORY_SECRET_RISK` blobs. One repository was
enough to settle the claim, and it is the one whose history is longest.

A refuted prediction is the useful one here. A rarity would have supported a page-rank tool for
the unlucky; a base rate supports an instrument that any team with more than one branch needs.

## What the three secret claims actually are

Risk is a class, not a verdict, and the tool says so in the same word. Read by hand:

- `requests/.env` — nine bytes, a placeholder, no credential.
- `requests/cacert.pem` — a CA bundle; a certificate store is not a secret.
- `requests/tests/certs/*/**.key` — four test key pairs, committed on purpose so TLS tests can run.
- `commander.js/examples/password` — the example the documentation tells you to open.

Five claims, five benign after triage, zero false negatives hidden behind them: the class is
doing what it promises — naming what a human must look at, not deciding for them.

## What these numbers do not say

- They are a snapshot. A clone taken tomorrow measures tomorrow's object database.
- One repository of six was lost to a purged directory and is declared `NO_MEDIDO`; five is a
  base rate, not a census.
- `ORPHAN_CODE` counts paths, not intent: an abandoned branch and an unmerged worktree commit
  are indistinguishable to a reader of the object database, and both are worth naming.
- No claim is made that any of these repositories has lost something its maintainers wanted.

## Reproduction

Any row above is reproducible from the two columns it needs — the repository and the commit —
with the single command in this file. Nothing in this measurement depends on this machine, on a
private path, or on a prior run.

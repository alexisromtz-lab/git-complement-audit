"""Hermetic fixture: builds a throwaway repository and audits it. No network, no user data.

The fixture is the only source of the numbers the README quotes, so the README cannot
drift from the instrument: ``--self-test`` recomputes them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import tempfile

from . import engine

FIXTURE_DATE = "2026-01-01T00:00:00 +0000"

BASE_FILES = {
    "app.py": "print('hello')\n",
    "README.md": "# demo\n",
    "docs/guide.md": "# guide\n",
}
SIDE_FILES = {
    "app_copy.py": "print('hello')\n",  # identical blob, different path -> MOVED
    "orphan/handler.py": "def handle():\n    return 1\n",  # -> ORPHAN_CODE
    ".env": "API_TOKEN=placeholder\n",  # -> ORPHAN_SECRET_RISK
    "docs/design.md": "# design\n",  # -> ORPHAN_DOC
    "build/out.bin": "\x00\x01\x02 binary\n",  # -> RESIDUE
}
WORKTREE_FILE = "scratch/agent_notes.py"
DANGLING_SECRET = "-----BEGIN RSA PRIVATE KEY-----\nnot-a-real-key\n"
AGENT_FILES = {
    "runtime/checkpoints/lost.py": "def checkpoint():\n    return 0\n",  # -> ORPHAN_CODE
    "runtime/checkpoints/.env": "AGENT_TOKEN=placeholder\n",  # -> ORPHAN_SECRET_RISK
}
EXPECTED = {
    engine.MOVED: 1,
    engine.ORPHAN_CODE: 3,
    engine.ORPHAN_SECRET_RISK: 2,
    engine.ORPHAN_DOC: 1,
    engine.RESIDUE: 1,
}


def _commit(repo: pathlib.Path, files: dict[str, str], message: str, parent: str | None = None,
            index: pathlib.Path | None = None) -> str:
    index = index or repo / ".git" / "self-test.index"
    env = {
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_DATE": FIXTURE_DATE,
        "GIT_COMMITTER_DATE": FIXTURE_DATE,
    }
    engine.git(repo, "read-tree", "--empty", env=env)
    for path, content in sorted(files.items()):
        oid = engine.git(repo, "hash-object", "-w", "--stdin", input=content, env=env).strip()
        engine.git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (oid, path), env=env)
    tree = engine.git(repo, "write-tree", env=env).strip()
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args += ["-p", parent]
    return engine.git(repo, *args, env=env).strip()


def _init(repo: pathlib.Path) -> str:
    repo.mkdir(parents=True)
    engine.git(repo, "init", "-q", "-b", "main")
    engine.git(repo, "config", "user.email", "self-test@example.invalid")
    engine.git(repo, "config", "user.name", "git-complement-audit self-test")
    commit = _commit(repo, BASE_FILES, "base")
    engine.git(repo, "update-ref", "refs/heads/main", commit)
    engine.git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return commit


def run() -> int:
    from . import cli

    checks: list[str] = []
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        checks.append(label)
        if not condition:
            failures.append(label)

    with tempfile.TemporaryDirectory() as box:
        base = pathlib.Path(box)
        repo = base / "repo"
        head = _init(repo)

        side = _commit(repo, SIDE_FILES, "hidden ref work", parent=head)
        engine.git(repo, "update-ref", "refs/custom/hidden", side)

        agent = _commit(repo, AGENT_FILES, "runtime checkpoint", parent=head)
        engine.git(repo, "update-ref", "refs/agents/turn/1", agent)

        worktree = base / "wt"
        engine.git(repo, "worktree", "add", "-q", "--detach", str(worktree))
        target = worktree / WORKTREE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def todo():\n    return None\n", encoding="utf-8")
        engine.git(worktree, "add", WORKTREE_FILE)
        engine.git(worktree, "commit", "-q", "-m", "detached work")

        report = engine.audit(repo)
        check("head is the fixture commit", report["repo"]["head"] == head)
        check("fixture is reproducible on any machine", _init(base / "twin") == head)
        check("object format declared", report["repo"]["object_format"] in ("sha1", "sha256"))
        check("five classes counted", report["counts"] == EXPECTED)
        check("worktree HEAD counted as a root", report["repo"]["worktrees"] >= 2)
        check("detached worktree commit is outside HEAD", report["repo"]["commits_outside_head"] >= 2)
        classes = {finding["class"] for finding in report["findings"]}
        check("no finding is missing a class", classes <= set(engine.CLASSES))
        check("moved file keeps its path out of HEAD", any(
            f["class"] == engine.MOVED and f["path"] == "app_copy.py" for f in report["findings"]))
        check("worktree file reported as orphan code", any(
            f["class"] == engine.ORPHAN_CODE and f["path"] == WORKTREE_FILE for f in report["findings"]))
        check("findings are ordered by class then path",
              report["findings"] == sorted(report["findings"],
                                           key=lambda f: (engine.CLASSES.index(f["class"]), f["path"], f["oid"])))
        check("verdict fails on orphan code", engine.has_failure(report))
        check("cli returns 1 on orphan code", cli.main([str(repo), "--quiet"]) == cli.RC_FINDINGS)

        excluded = engine.audit(repo, exclude_refs=("refs/custom/*",))
        check("--exclude-ref drops the excluded ref's findings",
              excluded["counts"][engine.MOVED] == 0 and excluded["counts"][engine.ORPHAN_DOC] == 0)
        check("--exclude-ref declares how many refs it left out",
              excluded["scope"]["refs excluded"] == 1)
        check("--exclude-ref keeps what the other roots still hold",
              excluded["counts"][engine.ORPHAN_CODE] == EXPECTED[engine.ORPHAN_CODE] - 1)
        check("--exclude-ref keeps the secret it excluded, and names its root", any(
            f["class"] == engine.ORPHAN_SECRET_RISK and f["path"] == ".env" and f["root"] == "excluded refs"
            for f in excluded["findings"]))
        check("--exclude-ref never returns RC 0 over a secret it excluded",
              cli.main([str(repo), "--quiet", "--exclude-ref", "refs/custom/*"]) == cli.RC_FINDINGS)

        runtime = engine.audit(repo, exclude_refs=("refs/agents/*",))
        check("--exclude-ref drops runtime code noise",
              runtime["counts"][engine.ORPHAN_CODE] == EXPECTED[engine.ORPHAN_CODE] - 1)
        check("a secret that lives only in the excluded namespace survives",
              runtime["counts"][engine.ORPHAN_SECRET_RISK] == EXPECTED[engine.ORPHAN_SECRET_RISK])
        check("hidden runtime namespaces are counted even when excluded",
              runtime["hidden_refs"]["refs/agents/"] == 1)

        opted_out = engine.audit(repo, exclude_refs=("refs/custom/*",), allow_excluded_secrets=True)
        check("--allow-excluded-secrets drops the excluded secret",
              opted_out["counts"][engine.ORPHAN_SECRET_RISK] == EXPECTED[engine.ORPHAN_SECRET_RISK] - 1)
        check("--allow-excluded-secrets is declared, never silent",
              opted_out["scope"]["excluded secret scan"].startswith("off"))

        narrow = engine.audit(repo, include_worktrees=False)
        check("--no-worktrees drops the worktree finding",
              narrow["counts"][engine.ORPHAN_CODE] == EXPECTED[engine.ORPHAN_CODE] - 1)

        plain = engine.audit(repo, include_unreachable=False)
        check("unreachable objects are out of the default scope",
              not any(f["path"].startswith("@unreachable/") for f in plain["findings"]))
        engine.git(repo, "hash-object", "-w", "--stdin", input=DANGLING_SECRET)
        wide = engine.audit(repo, include_unreachable=True)
        check("--unreachable surfaces a dangling secret blob", any(
            f["class"] == engine.ORPHAN_SECRET_RISK and f["path"].startswith("@unreachable/")
            for f in wide["findings"]))

        rescue_dir = base / "rescue"
        rows = engine.rescue(repo, report, rescue_dir)
        check("rescue writes code, secret and doc",
              len(rows) == sum(EXPECTED[kind] for kind in engine.RESCUABLE))
        check("rescue skips moved and residue",
              {row["path"].split("/", 1)[0] for row in rows} == {"ORPHAN_CODE", "ORPHAN_SECRET_RISK", "ORPHAN_DOC"})
        check("rescue verifies every re-hash",
              all(engine.object_id((rescue_dir / row["path"]).read_bytes(), report["repo"]["object_format"]) == row["oid"]
                  for row in rows))
        sums = (rescue_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        check("SHA256SUMS has one line per rescued file", len(sums) == len(rows))
        check("rescue directory is 0700", (os.stat(rescue_dir).st_mode & 0o777) == 0o700)
        check("rescued files are 0600",
              all((os.stat(rescue_dir / row["path"]).st_mode & 0o777) == 0o600 for row in rows))

        first, second = base / "receipt-a.json", base / "receipt-b.json"
        engine.receipt(repo, report, first)
        engine.receipt(repo, report, second)
        check("receipt is deterministic", first.read_bytes() == second.read_bytes())
        check("receipt carries a sha256 per finding",
              all("sha256" in finding for finding in json.loads(first.read_text(encoding="utf-8"))["findings"]))

        clean = base / "clean"
        _init(clean)
        clean_report = engine.audit(clean)
        check("clean repository reports nothing", sum(clean_report["counts"].values()) == 0)
        check("clean repository returns RC 0", cli.main([str(clean), "--quiet"]) == cli.RC_CLEAN)

        docs = _init(base / "docs-only")
        docs_side = _commit(base / "docs-only", {"notes/plan.md": "# plan\n"}, "docs only", parent=docs)
        engine.git(base / "docs-only", "update-ref", "refs/custom/docs", docs_side)
        check("orphan documentation does not fail the gate",
              cli.main([str(base / "docs-only"), "--quiet"]) == cli.RC_CLEAN)

        not_a_repo = base / "not-a-repo"
        not_a_repo.mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            invalid = cli.main([str(not_a_repo), "--quiet"])
        check("invalid repository returns RC 2", invalid == cli.RC_CONTRACT)

        try:
            engine.audit(base / "does-not-exist")
        except engine.ContractError:
            check("missing path raises the contract error", True)
        else:
            check("missing path raises the contract error", False)

    if failures:
        print("SELF-TEST FAILED: %d of %d checks" % (len(failures), len(checks)))
        for label in failures:
            print("  FAIL  %s" % label)
        return 1

    print("SELF-TEST passed: %d checks on a synthetic repository" % len(checks))
    print("fixture classes: %s" % " · ".join("%s=%d" % (kind, EXPECTED[kind]) for kind in engine.CLASSES))
    print(
        "fixture verdict: RC 1 · rescue %d files re-hashed · receipt byte-identical · "
        "clean RC 0 · invalid RC 2" % len(rows)
    )
    return 0

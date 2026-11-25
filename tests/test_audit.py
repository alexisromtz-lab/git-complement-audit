"""Contract tests at the CLI boundary: exit codes, receipt determinism and rescue permissions.

The in-process checks of ``--self-test`` cover the engine; these cover what a caller
actually sees — the process exit code and the files it is allowed to leave behind.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATE = "2026-01-01T00:00:00 +0000"


def _clean_env(**extra: str) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "PYTHONPATH"))
    }
    env.update(extra)
    return env


def git(repo: pathlib.Path, *args: str, env: dict | None = None, input: str | None = None) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        env=env if env is not None else _clean_env(),
        input=input,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args[:2]), done.stderr.strip()))
    return done.stdout


def commit(repo: pathlib.Path, index: pathlib.Path, files: dict[str, str], message: str,
           parent: str | None = None) -> str:
    env = _clean_env(
        GIT_INDEX_FILE=str(index),
        GIT_AUTHOR_DATE=DATE,
        GIT_COMMITTER_DATE=DATE,
    )
    git(repo, "read-tree", "--empty", env=env)
    for path, content in sorted(files.items()):
        oid = git(repo, "hash-object", "-w", "--stdin", env=env, input=content).strip()
        git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (oid, path), env=env)
    tree = git(repo, "write-tree", env=env).strip()
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args += ["-p", parent]
    return git(repo, *args, env=env).strip()


def make_repo(box: pathlib.Path, hidden: dict[str, str] | None = None,
              hidden_ref: str = "refs/custom/hidden") -> pathlib.Path:
    repo = box / ("repo-%d" % len(list(box.iterdir())))
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "user.name", "tests")
    index = repo / ".git" / "test.index"
    head = commit(repo, index, {"app.py": "print('hello')\n"}, "base")
    git(repo, "update-ref", "refs/heads/main", head)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    if hidden:
        side = commit(repo, index, hidden, "hidden", parent=head)
        git(repo, "update-ref", hidden_ref, side)
    return repo


def audit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "git_complement_audit", *args],
        cwd=str(ROOT),
        env=_clean_env(PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
    )


class ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.box = pathlib.Path(tempfile.mkdtemp(prefix="gca-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.box, ignore_errors=True)

    def test_self_test_passes(self) -> None:
        done = audit("--self-test")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("SELF-TEST passed", done.stdout)

    def test_clean_repository_exits_zero(self) -> None:
        repo = make_repo(self.box)
        done = audit(str(repo), "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(set(report["counts"].values()), {0})
        self.assertEqual(report["findings"], [])

    def test_hidden_env_file_exits_one(self) -> None:
        repo = make_repo(self.box, {"deep/.env": "TOKEN=1\n"})
        done = audit(str(repo))
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("ORPHAN_SECRET_RISK", done.stdout)
        report = json.loads(audit(str(repo), "--json").stdout)
        secrets = [f["path"] for f in report["findings"] if f["class"] == "ORPHAN_SECRET_RISK"]
        self.assertEqual(secrets, ["deep/.env"])

    def test_credential_at_a_path_that_still_exists_in_head_is_declared_and_read(self) -> None:
        repo = make_repo(self.box, {"app.py": "KEY = '%s%s'\n" % ("AKIA", "IOSFODNN7EXAMPLE")})
        silent = audit(str(repo), "--json")
        self.assertEqual(silent.returncode, 0, silent.stderr)
        report = json.loads(silent.stdout)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["counts"]["ORPHAN_SECRET_RISK"], 0)
        self.assertEqual(report["scope"]["history scan"], "off")
        self.assertEqual(report["universe"]["blobs_not_named"], 1)
        printed = audit(str(repo))
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertIn("are named by no path row", printed.stdout)

        found = audit(str(repo), "--json", "--scan-history")
        self.assertEqual(found.returncode, 1, found.stderr)
        report = json.loads(found.stdout)
        self.assertEqual(
            [(f["class"], f["path"]) for f in report["findings"]],
            [("HISTORY_SECRET_RISK", "app.py")],
        )
        self.assertEqual(report["universe"]["blobs_not_named"], 1)

    def test_clean_repository_declares_nothing_unread(self) -> None:
        repo = make_repo(self.box)
        report = json.loads(audit(str(repo), "--json").stdout)
        self.assertEqual(report["universe"]["blobs_outside_head"], 0)
        self.assertEqual(report["universe"]["blobs_not_named"], 0)
        self.assertEqual(report["scope"]["history scan"], "off")
        done = audit(str(repo), "--scan-history")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_exclude_ref_drops_noise_but_never_a_secret(self) -> None:
        repo = make_repo(self.box, {"deep/.env": "TOKEN=1\n", "deep/lost.py": "x = 1\n"})
        done = audit(str(repo), "--json", "--exclude-ref", "refs/custom/*")
        self.assertEqual(done.returncode, 1, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["scope"]["refs excluded"], 1)
        self.assertEqual(report["scope"]["excluded secret scan"], "on")
        self.assertEqual(report["counts"]["ORPHAN_CODE"], 0)
        secrets = [(f["path"], f["root"]) for f in report["findings"]
                   if f["class"] == "ORPHAN_SECRET_RISK"]
        self.assertEqual(secrets, [("deep/.env", "excluded refs")])

    def test_allow_excluded_secrets_opts_out_and_declares_it(self) -> None:
        repo = make_repo(self.box, {"deep/.env": "TOKEN=1\n"})
        done = audit(str(repo), "--json", "--exclude-ref", "refs/custom/*", "--allow-excluded-secrets")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["counts"]["ORPHAN_SECRET_RISK"], 0)
        self.assertEqual(report["scope"]["excluded secret scan"], "off (--allow-excluded-secrets)")

    def test_json_counts_hidden_runtime_namespaces(self) -> None:
        repo = make_repo(self.box, {"notes/todo.py": "x = 1\n"}, hidden_ref="refs/agents/turn/1")
        report = json.loads(audit(str(repo), "--json", "--exclude-ref", "refs/agents/*").stdout)
        self.assertEqual(report["hidden_refs"]["refs/agents/"], 1)
        self.assertEqual(report["hidden_refs"]["refs/original/"], 0)

    def test_invalid_repository_exits_two(self) -> None:
        plain = self.box / "plain"
        plain.mkdir()
        done = audit(str(plain))
        self.assertEqual(done.returncode, 2)
        self.assertIn("CONTRACT", done.stderr)

    def test_receipt_is_byte_identical_between_runs(self) -> None:
        repo = make_repo(self.box, {"notes/todo.py": "x = 1\n"})
        first, second = self.box / "a.json", self.box / "b.json"
        self.assertEqual(audit(str(repo), "--receipt", str(first), "--quiet").returncode, 1)
        audit(str(repo), "--receipt", str(second), "--quiet")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_a_declared_exemption_moves_a_secret_out_of_the_gate(self) -> None:
        repo = make_repo(self.box, {"deep/.env": "TOKEN=1\n"})
        self.assertEqual(audit(str(repo), "--quiet").returncode, 1)
        oid = git(repo, "hash-object", "--stdin", input="TOKEN=1\n").strip()
        declaration = repo / ".git-complement-audit-ignore"
        declaration.write_text("%s  # rotated upstream\n" % oid, encoding="utf-8")

        done = audit(str(repo), "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["counts"]["ORPHAN_SECRET_RISK"], 0)
        self.assertEqual(report["counts"]["EXEMPT_SECRET"], 1)
        self.assertEqual(report["exemptions"], {"file": ".git-complement-audit-ignore",
                                                "declared": 1, "applied": 1, "unused": []})
        self.assertEqual([(f["class"], f["declared"]) for f in report["findings"]],
                         [("EXEMPT_SECRET", "ORPHAN_SECRET_RISK")])
        printed = audit(str(repo))
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertIn("EXEMPT", printed.stdout)

        declaration.write_text("%s-not-a-blob-id\n" % "AKIA", encoding="utf-8")
        broken = audit(str(repo), "--quiet")
        self.assertEqual(broken.returncode, 2)
        self.assertIn("CONTRACT", broken.stderr)

    def test_an_exemption_cannot_silence_orphan_code(self) -> None:
        repo = make_repo(self.box, {"lost/handler.py": "def f():\n    return 1\n"})
        oid = git(repo, "hash-object", "--stdin", input="def f():\n    return 1\n").strip()
        (repo / ".git-complement-audit-ignore").write_text("%s\n" % oid, encoding="utf-8")
        done = audit(str(repo), "--json")
        self.assertEqual(done.returncode, 1, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["counts"]["ORPHAN_CODE"], 1)
        self.assertEqual(report["counts"]["EXEMPT_SECRET"], 0)
        self.assertEqual(report["exemptions"]["applied"], 0)
        self.assertEqual(report["exemptions"]["unused"], [oid])

    def test_rescue_writes_private_files_verifiable_outside_the_object_database(self) -> None:
        repo = make_repo(self.box, {"deep/.env": "TOKEN=1\n", "lost/handler.py": "def f():\n    pass\n"})
        out = self.box / "rescue"
        done = audit(str(repo), "--rescue-dir", str(out), "--quiet")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        rescued = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        self.assertEqual(
            rescued,
            [
                "MANIFEST.json",
                "ORPHAN_CODE/lost/handler.py",
                "ORPHAN_SECRET_RISK/deep/.env",
                "SHA256SUMS",
            ],
        )
        for path in out.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
        self.assertEqual(len((out / "SHA256SUMS").read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()

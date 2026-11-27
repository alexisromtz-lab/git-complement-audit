"""Census of the object-database complement: every path reachable by Git but not by HEAD.

The subject is deliberately narrow and declared: paths that some ref or worktree HEAD
reaches and that ``HEAD`` does not contain. Paths that still exist in ``HEAD`` are never
reported, even when their content differs, because a moved or edited file is not a
missing file.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import pathlib
import subprocess

from . import __version__

MOVED = "MOVED"
ORPHAN_CODE = "ORPHAN_CODE"
ORPHAN_SECRET_RISK = "ORPHAN_SECRET_RISK"
ORPHAN_DOC = "ORPHAN_DOC"
RESIDUE = "RESIDUE"
HISTORY_SECRET_RISK = "HISTORY_SECRET_RISK"
EXEMPT_SECRET = "EXEMPT_SECRET"

CLASSES = (
    MOVED,
    ORPHAN_CODE,
    ORPHAN_SECRET_RISK,
    ORPHAN_DOC,
    RESIDUE,
    HISTORY_SECRET_RISK,
    EXEMPT_SECRET,
)
RESCUABLE = (ORPHAN_CODE, ORPHAN_SECRET_RISK, ORPHAN_DOC)
FAILING = (ORPHAN_CODE, ORPHAN_SECRET_RISK, HISTORY_SECRET_RISK)
# An exemption is only ever granted to a claim of danger. A blob that is *absent* from
# HEAD is evidence of loss, and no declaration in the audited repository may quiet it.
EXEMPTABLE = (ORPHAN_SECRET_RISK, HISTORY_SECRET_RISK)

CODE_EXTENSIONS = frozenset(
    {
        ".py", ".pyi", ".sh", ".bash", ".zsh", ".mjs", ".cjs", ".js", ".jsx",
        ".ts", ".tsx", ".rb", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc",
        ".cpp", ".hpp", ".cs", ".php", ".pl", ".lua", ".swift", ".scala",
        ".sql", ".yml", ".yaml", ".toml", ".tf", ".dockerfile", ".mk",
    }
)
DOC_EXTENSIONS = frozenset(
    {".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".txt", ".org", ".tex"}
)
CODE_NAMES = frozenset({"Dockerfile", "Makefile", "Jenkinsfile", "BUILD", "WORKSPACE"})

SECRET_NAMES = frozenset(
    {
        ".env", ".netrc", ".npmrc", ".pypirc", ".htpasswd", ".git-credentials",
        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "secrets",
        "secret", "master.key", "wp-config.php",
    }
)
SECRET_PREFIXES = (".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk", ".ovpn")
SECRET_WORDS = ("secret", "credential", "password", "passwd", "apikey", "api_key", "privatekey")
SECRET_WORD_EXTENSIONS = frozenset({"", ".json", ".yml", ".yaml", ".txt", ".ini", ".cfg", ".conf", ".toml", ".xml"})
SECRET_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".docker", "secrets", "private"})

def _pem(label: bytes) -> bytes:
    """A PEM armour line assembled from parts.

    Spelling the armour out as a literal would put the detector's own dictionary inside
    the detector, and every historical copy of this file would match it: a relation that
    satisfies its own definition names no credential, it names itself.
    """
    return b"-" * 5 + b"BEGIN " + label + b"-" * 5


SECRET_MARKERS = tuple(
    _pem(label)
    for label in (
        b"RSA PRIVATE KEY",
        b"DSA PRIVATE KEY",
        b"EC PRIVATE KEY",
        b"OPENSSH PRIVATE KEY",
        b"PGP PRIVATE KEY BLOCK",
        b"PRIVATE KEY",
    )
)
# A prefix alone is a hint, not a finding: `b"AKIA"` occurs in this very table, in prose,
# and in half the world's tutorials. A real token carries a body of a known length, and
# only that body makes the claim.
TOKEN_SHAPES = (
    (b"AKIA", 16),
    (b"ASIA", 16),
    (b"ghp_", 20),
    (b"ghs_", 20),
    (b"github_pat_", 20),
    (b"xoxb-", 12),
    (b"xoxp-", 12),
    (b"sk-ant-", 20),
    (b"sk-proj-", 20),
    (b"glpat-", 18),
    (b"AIza", 30),
)
_TOKEN_BODY = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
SNIFF_BYTES = 65536
MAX_TIP_LISTINGS = 200

# Age of the newest ref tip that reaches a path outside HEAD. Three buckets answer the
# question a maintainer asks in a pull request: is this work still moving, or is it archive?
STRATUM_LIVE_DAYS = 30
STRATUM_RECENT_DAYS = 90
STRATA = ("live", "recent", "stale")

REF_FORMAT = (
    "%(refname)%00%(objecttype)%00%(objectname)%00%(*objectname)"
    "%00%(committerdate:unix)%00%(*committerdate:unix)"
)

# Exemptions are keyed by object id, never by name or glob: granting one costs a content
# address, so it cannot be handed to a directory, and it cannot outlive the bytes it names.
IGNORE_NAME = ".git-complement-audit-ignore"

# Namespaces an agent runtime writes on its own. They are counted even when they are
# excluded, because "clean" must not mean "I stopped looking".
HIDDEN_NAMESPACES = ("refs/agents/", "refs/checkpoints/", "refs/original/")

_GIT_DROP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


class ContractError(RuntimeError):
    """The repository or the arguments do not allow a measurement."""


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_DROP}
    if extra:
        env.update(extra)
    return env


def git(repo, *args, input=None, env=None, text=True):
    """Run git with a clean object-database environment; fail closed on error."""
    if text and isinstance(input, bytes):
        input = input.decode("utf-8", "replace")
    done = subprocess.run(
        ("git", "-C", str(repo), *args),
        input=input,
        capture_output=True,
        text=text,
        env=_env(env),
        check=False,
    )
    if done.returncode != 0:
        stderr = done.stderr if text else done.stderr.decode("utf-8", "replace")
        raise ContractError("git %s -> %s" % (" ".join(args[:2]), stderr.strip()[:300] or "exit %d" % done.returncode))
    return done.stdout


def git_maybe(repo, *args, text=True):
    try:
        return git(repo, *args, text=text)
    except ContractError:
        return None


def object_format(repo) -> str:
    out = git_maybe(repo, "rev-parse", "--show-object-format")
    return (out or "").strip() or "sha1"


def object_id(data: bytes, fmt: str) -> str:
    """Recompute the object name of a blob from its bytes, outside the ODB."""
    digest = hashlib.sha256() if fmt == "sha256" else hashlib.sha1()
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def worktree_heads(repo) -> list[str]:
    """Every worktree HEAD, including detached ones that no ref points at."""
    out = git_maybe(repo, "worktree", "list", "--porcelain") or ""
    return [line[5:].strip() for line in out.splitlines() if line.startswith("HEAD ")]


def _object_types(repo, oids) -> dict[str, tuple[str, int]]:
    if not oids:
        return {}
    payload = "".join(oid + "\n" for oid in oids)
    out = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", input=payload)
    types = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            types[parts[0]] = (parts[1], int(parts[2]))
    return types


def _blob_index(repo, listing: str) -> tuple[dict[str, str], dict[str, str]]:
    """`<oid> <path>` lines reduced to blobs, by path and by object.

    The path view is what a row can name, and it holds one blob per path. The object view
    is the denominator: two versions of one path are two blobs, and the one the path view
    dropped is still in the object database.
    """
    candidates: dict[str, str] = {}
    first_path: dict[str, str] = {}
    for line in listing.splitlines():
        oid, _, path = line.partition(" ")
        if oid:
            first_path.setdefault(oid, path)
        if path and not path.startswith("@"):
            candidates[path] = oid
    types = _object_types(repo, sorted(set(candidates.values()) | set(first_path)))
    by_path = {path: oid for path, oid in candidates.items() if types.get(oid, ("", 0))[0] == "blob"}
    by_oid = {oid: path for oid, path in first_path.items() if types.get(oid, ("", 0))[0] == "blob"}
    return by_path, by_oid


def _contents(repo, oids) -> dict[str, bytes]:
    """Blob prefixes for many objects in one process; `--batch` is size-prefixed, so binary is safe."""
    if not oids:
        return {}
    payload = "".join(oid + "\n" for oid in oids).encode("utf-8")
    out = git(repo, "cat-file", "--batch", input=payload, text=False)
    contents: dict[str, bytes] = {}
    cursor = 0
    while cursor < len(out):
        newline = out.find(b"\n", cursor)
        if newline < 0:
            break
        header = out[cursor:newline].split()
        cursor = newline + 1
        if len(header) != 3:
            continue
        size = int(header[2])
        contents[header[0].decode("ascii")] = out[cursor:cursor + size][:SNIFF_BYTES]
        cursor += size + 1
    return contents


def _tree_blobs(listing: str) -> dict[str, str]:
    """`<mode> <type> <oid>\\t<path>` from `ls-tree -r` -> {path: oid}, blobs only.

    Unlike an object walk, `ls-tree` names every path, so a file whose bytes already
    live at another path is still visible and can be classified as MOVED.
    """
    paths = {}
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and path:
            paths[path] = parts[2]
    return paths


def _all_blob_ids(repo) -> set[str]:
    """Every blob physically in the object database, reachable from a ref or not.

    `rev-list --all` answers what a ref reaches; this answers what the repository holds.
    The two differ exactly for the objects `git gc --prune` deletes without ever having
    named them, which is the population an audit must not be silent about.
    """
    out = git(repo, "cat-file", "--batch-all-objects", "--batch-check")
    rows = (line.split() for line in out.splitlines())
    return {row[0] for row in rows if len(row) == 3 and row[1] == "blob"}


def _ref_rows(repo) -> list[tuple[str, str, str, str, str, str]]:
    """Every ref as (name, type, id, peeled id, date, peeled date): one census for one walk.

    The peeled pair is what an annotated tag adds: `objectname` is the tag and carries no
    committer date, `*objectname` is the commit it points at and carries the date the age
    strata need. Reading both here keeps the census a single `for-each-ref`.
    """
    out = git_maybe(repo, "for-each-ref", "--format=" + REF_FORMAT) or ""
    rows = []
    for line in out.splitlines():
        parts = line.split("\0")
        if len(parts) == 6 and parts[0]:
            rows.append(tuple(parts))
    return rows


def _classify_refs(rows, exclude=()) -> tuple[list[str], list[str], int]:
    """Tips under audit, tips left out on purpose, and how many refs matched a pattern.

    A repository driven by an agent runtime keeps a snapshot ref per turn; those refs
    dominate the complement and hide the operator's own lost work. Excluding them is a
    declared choice, so the excluded tips are returned instead of dropped.
    """
    tips, skipped, matched = set(), set(), 0
    for name, kind, oid, _, _, _ in rows:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in exclude):
            matched += 1
            if kind == "commit" and oid:
                skipped.add(oid)
        elif kind == "commit" and oid:
            tips.add(oid)
    return sorted(tips), sorted(skipped), matched


def ref_tips(repo, exclude=()) -> tuple[list[str], list[str]]:
    tips, skipped, _ = _classify_refs(_ref_rows(repo), exclude)
    return tips, skipped


def commit_date(repo, oid) -> int | None:
    """Committer date of one commit, in seconds; None when the object is not a commit."""
    out = (git_maybe(repo, "log", "-1", "--format=%ct", oid, "--") or "").strip()
    return int(out) if out.isdigit() else None


def _dated_roots(rows, exclude=(), extras=()) -> tuple[dict[str, int], list[str], list[tuple[str, int | None]]]:
    """Commit -> newest tip date, for every ref the audit keeps; annotated tags are peeled.

    A ref whose object carries no committer date (a tag of a tree, an unborn branch) is
    returned separately instead of being dated by guess: a stratum that cannot say when
    the work moved is not evidence that it moved.

    The per-ref census comes back with the roots because the two populations differ: many
    refs can name one commit, and a count of distinct tip dates is not a count of refs. Worktree
    HEADs date rows but are not refs, so they stay out of the census: the census must equal the
    repository's own ref count, or a reader cannot add it up.
    """
    roots: dict[str, int] = {}
    undated: list[str] = []
    census: list[tuple[str, int | None]] = []
    for name, kind, oid, peeled, date, peeled_date in rows:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in exclude):
            continue
        if kind == "commit" and date:
            roots[oid] = max(roots.get(oid, 0), int(date))
            census.append((name, int(date)))
        elif kind == "tag" and peeled and peeled_date:
            roots[peeled] = max(roots.get(peeled, 0), int(peeled_date))
            census.append((name, int(peeled_date)))
        elif kind in ("commit", "tag"):
            undated.append(name)
            census.append((name, None))
    for oid, date in extras:
        if date:
            roots[oid] = max(roots.get(oid, 0), int(date))
        else:
            undated.append("@worktree/" + oid[:12])
    return roots, undated, census


def _stratum(age: int) -> str:
    if age <= STRATUM_LIVE_DAYS:
        return "live"
    if age <= STRATUM_RECENT_DAYS:
        return "recent"
    return "stale"


def age_strata(findings, tip_dates, as_of, source, undated=(), listed=True, census=()) -> dict:
    """Split the rows outside HEAD by the age of the newest tip that still holds them.

    The buckets are a partition of `root: "refs"`, never a second count of the whole report:
    rows reached by an excluded ref, by history or by no ref at all carry their own `root`
    and are not dated here. A row that no tip tree holds survives only in commits the
    branches have since moved past — `history_only`, the archive the branches left behind —
    and is published apart instead of being folded into `stale`, because "no branch holds
    it" and "an old branch holds it" are two different sentences to a maintainer.

    The `refs` block answers the other half of the same question and is a partition of the
    census, not of the rows: how many refs moved in each window, whatever their paths are.
    """
    rows = [finding for finding in findings if finding["root"] == "refs"]
    if not listed:
        return {
            "state": "NO_MEDIDO",
            "reason": "per-tip listing skipped: no tip tree to date the rows against",
            "rows": len(rows),
        }
    if as_of is None:
        return {
            "state": "NO_MEDIDO",
            "reason": "no HEAD commit to date the strata against, and no --as-of given",
            "rows": len(rows),
        }
    buckets = {name: 0 for name in STRATA}
    history_only = 0
    future = 0
    for finding in rows:
        when = tip_dates.get((finding["path"], finding["oid"]))
        if when is None:
            history_only += 1
            continue
        age = (as_of - when) // 86400
        if age < 0:
            # A tip dated after the reference is counted where it is: the reference is a
            # commit date, not a clock, and skew must not become a fourth bucket.
            future += 1
            age = 0
        buckets[_stratum(age)] += 1
    refs = {name: 0 for name in STRATA}
    refs_undated = 0
    refs_future = 0
    for _, when in census:
        if when is None:
            refs_undated += 1
            continue
        age = (as_of - when) // 86400
        if age < 0:
            refs_future += 1
            age = 0
        refs[_stratum(age)] += 1
    block = {
        "state": "MEASURED",
        "reference": "newest ref tip whose tree holds the row's path with the row's content",
        "as_of": as_of,
        "as_of_source": source,
        "bounds_days": [STRATUM_LIVE_DAYS, STRATUM_RECENT_DAYS],
        "rows": len(rows),
        "history_only": history_only,
        "future_dated_rows": future,
        "undated_refs": list(undated)[:8],
        "conservation": sum(buckets.values()) + history_only == len(rows),
        "refs": {
            "total": len(census),
            "undated": refs_undated,
            "future_dated": refs_future,
            "conservation": sum(refs.values()) + refs_undated == len(census),
        },
    }
    block.update(buckets)
    block["refs"].update(refs)
    return block


def is_secret_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return False
    name = parts[-1].lower()
    if any(part.lower() in SECRET_DIRS for part in parts[:-1]):
        return True
    if name in SECRET_NAMES or name.startswith(SECRET_PREFIXES):
        return True
    if any(name.endswith(suffix) for suffix in SECRET_SUFFIXES):
        return True
    stem, ext = os.path.splitext(name)
    if ext in SECRET_WORD_EXTENSIONS and any(word in stem for word in SECRET_WORDS):
        return True
    return False


def _shaped_token(head: bytes) -> bool:
    for prefix, minimum in TOKEN_SHAPES:
        at = head.find(prefix)
        while at >= 0:
            body = head[at + len(prefix) : at + len(prefix) + minimum]
            if len(body) == minimum and all(char in _TOKEN_BODY for char in body):
                return True
            at = head.find(prefix, at + 1)
    return False


def looks_secret(data: bytes) -> bool:
    head = data[:SNIFF_BYTES]
    if any(marker in head for marker in SECRET_MARKERS):
        return True
    return _shaped_token(head)


def read_exemptions(root, path=None) -> tuple[dict[str, str], str]:
    """`{oid: "file:line"}` from the repository's own declaration file. Reads, never writes."""
    if path is None:
        target = pathlib.Path(root) / IGNORE_NAME
        if not target.is_file():
            return {}, ""
        label = IGNORE_NAME
    else:
        target = pathlib.Path(path).expanduser()
        if not target.is_file():
            raise ContractError("exemption file not found: %s" % path)
        label = target.name
    exempt: dict[str, str] = {}
    for number, line in enumerate(target.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        text = line.split("#", 1)[0].strip().lower()
        if not text:
            continue
        if any(char not in "0123456789abcdef" for char in text):
            raise ContractError("%s:%d is not a blob id: %s" % (label, number, text[:16]))
        exempt[text] = "%s:%d" % (label, number)
    return exempt, label


def _is_code(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in CODE_NAMES or os.path.splitext(name)[1].lower() in CODE_EXTENSIONS


def _is_doc(path: str) -> bool:
    return os.path.splitext(path.rsplit("/", 1)[-1])[1].lower() in DOC_EXTENSIONS


def audit(
    repo,
    include_worktrees: bool = True,
    include_unreachable: bool = False,
    max_tip_listings: int = MAX_TIP_LISTINGS,
    exclude_refs=(),
    allow_excluded_secrets: bool = False,
    scan_history: bool = False,
    ignore_file=None,
    as_of: int | None = None,
) -> dict:
    """Measure the complement. Writes nothing; every number here is a reading.

    Two readings are merged on purpose. The object walk covers the whole history but
    names one path per distinct blob; the per-tip `ls-tree` pass names every path of
    every ref tip and worktree HEAD, which is where content about to be lost lives.
    """
    root = pathlib.Path(repo).expanduser()
    if not root.is_dir():
        raise ContractError("not a directory: %s" % repo)
    top = git_maybe(root, "rev-parse", "--show-toplevel")
    if top is None:
        raise ContractError("not a git repository: %s" % repo)
    root = pathlib.Path(top.strip())
    # Read the declaration before measuring: a malformed line must fail the run, not a row.
    exempt, exempt_label = read_exemptions(root, ignore_file)
    fmt = object_format(root)
    head = (git_maybe(root, "rev-parse", "--verify", "HEAD") or "").strip()

    head_paths = _tree_blobs(git(root, "ls-tree", "-r", head)) if head else {}
    head_oids = set(head_paths.values())

    worktrees = sorted({sha for sha in worktree_heads(root) if sha})
    extras = [sha for sha in worktrees if sha != head]

    rows = _ref_rows(root)
    tips, excluded, excluded_refs = _classify_refs(rows, tuple(exclude_refs))
    hidden = {ns: sum(1 for row in rows if row[0].startswith(ns)) for ns in HIDDEN_NAMESPACES}

    # Age strata: a row is dated by the newest ref tip whose tree holds that path with that
    # content. It is a question of presence, not of freshness — a file untouched for years
    # inside a branch moved yesterday is live work, and the reader of a pull request wants
    # to see it — so the tip trees answer it and no per-row `git log` is needed.
    dated_extras = [(sha, commit_date(root, sha)) for sha in extras] if include_worktrees else []
    roots, undated, census = _dated_roots(rows, tuple(exclude_refs), dated_extras)
    tip_dates: dict[tuple[str, str], int] = {}

    walk = ["rev-list", "--objects", "--all"]
    skipped = list(excluded) + ([] if include_worktrees else extras)
    if skipped:
        walk += ["--not", *sorted(set(skipped))]
    complement, seen = _blob_index(root, git(root, *walk))

    tips += extras if include_worktrees else []
    exact_tips = sorted(set(tips))
    listed_exactly = len(exact_tips) <= max_tip_listings
    if listed_exactly:
        for tip in exact_tips:
            listing = git_maybe(root, "ls-tree", "-r", tip)
            if listing:
                when = roots.get(tip)
                for path, oid in _tree_blobs(listing).items():
                    complement.setdefault(path, oid)
                    seen.setdefault(oid, path)
                    if when is not None:
                        known = tip_dates.get((path, oid))
                        tip_dates[(path, oid)] = when if known is None else max(known, when)

    findings = []
    for path in sorted(complement):
        if path in head_paths:
            continue
        oid = complement[path]
        if oid in head_oids:
            kind = MOVED
        elif is_secret_path(path):
            kind = ORPHAN_SECRET_RISK
        elif _is_code(path):
            kind = ORPHAN_CODE
        elif _is_doc(path):
            kind = ORPHAN_DOC
        else:
            kind = RESIDUE
        findings.append({"class": kind, "path": path, "oid": oid, "root": "refs"})

    excluded_scan = "not requested"
    if exclude_refs:
        excluded_scan = "off (--allow-excluded-secrets)" if allow_excluded_secrets else "on"
    origin: dict[str, str] = {}
    # Blobs whose path the report holds back because it sits outside HEAD in an excluded
    # ref: they are named, so they are not the unread population the history scan is for.
    named_but_unpublished: set[str] = set()
    if excluded and excluded_scan == "on":
        # `--not` would drop every blob shared with the included roots, and a secret shared
        # with one would disappear; walking the excluded tips alone keeps them visible.
        listing = git(root, "rev-list", "--objects", "--stdin", input="\n".join(excluded) + "\n")
        excluded_by_path, excluded_blobs = _blob_index(root, listing)
        for oid, path in excluded_blobs.items():
            seen.setdefault(oid, path)
            origin.setdefault(oid, "excluded refs")
        taken = {(f["class"], f["path"], f["oid"]) for f in findings}
        for path, oid in excluded_by_path.items():
            if path in head_paths:
                continue
            named_but_unpublished.add(oid)
            if oid in head_oids or not is_secret_path(path):
                continue
            if (ORPHAN_SECRET_RISK, path, oid) in taken:
                continue
            findings.append(
                {"class": ORPHAN_SECRET_RISK, "path": path, "oid": oid, "root": "excluded refs"}
            )

    unreachable = 0
    if include_unreachable:
        listing = git_maybe(root, "fsck", "--unreachable", "--no-reflogs", "--no-progress") or ""
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[1] != "blob":
                continue
            oid = parts[2]
            unreachable += 1
            path = "@unreachable/" + oid
            content = git(root, "cat-file", "blob", oid, text=False)[:SNIFF_BYTES]
            findings.append(
                {
                    "class": ORPHAN_SECRET_RISK if looks_secret(content) else RESIDUE,
                    "path": path,
                    "oid": oid,
                    "root": "unreachable",
                }
            )

    named = {finding["oid"] for finding in findings} | named_but_unpublished
    outside_head = {oid for oid in seen if oid not in head_oids}
    unnamed = sorted(outside_head - named)
    if scan_history and unnamed:
        # A credential whose path still exists in HEAD is not an orphan path, but it is in
        # every clone. The content is read only here, and only for the blobs no row names.
        for oid, content in _contents(root, unnamed).items():
            if looks_secret(content):
                findings.append(
                    {
                        "class": HISTORY_SECRET_RISK,
                        "path": seen.get(oid) or "@history/" + oid,
                        "oid": oid,
                        "root": origin.get(oid, "history"),
                    }
                )

    sizes = _object_types(root, sorted({f["oid"] for f in findings}))
    for finding in findings:
        finding["size"] = sizes.get(finding["oid"], ("", 0))[1]

    # Closure: HEAD plus the complement must be the database, or the report is narrower than
    # the repository it claims to measure. The gap can only be objects no ref reaches, which
    # --unreachable names; it is published either way, and it never invents a fourth exit code.
    database = _all_blob_ids(root)
    covered = set(outside_head) | {f["oid"] for f in findings if f["root"] == "unreachable"}
    uncovered = sorted(database - head_oids - covered)
    closure = {
        "odb_blobs": len(database),
        "head_blobs": len(head_oids & database),
        "covered_blobs": len(covered & database),
        "uncovered_blobs": len(uncovered),
        "uncovered_sample": [oid[:12] for oid in uncovered[:8]],
    }

    # An exemption is granted to a claim of danger, by content address, and only after the
    # claim has been made: the class it replaces stays recorded so the act is auditable.
    applied: list[str] = []
    for finding in findings:
        if finding["class"] not in EXEMPTABLE:
            continue
        locus = exempt.get(finding["oid"])
        if locus is None:
            continue
        finding["declared"] = finding["class"]
        finding["class"] = EXEMPT_SECRET
        finding["exemption"] = locus
        applied.append(finding["oid"])
    spent = set(applied)
    exemptions = {
        "file": exempt_label,
        "declared": len(exempt),
        "applied": len(applied),
        "unused": sorted(oid for oid in exempt if oid not in spent),
    }

    # Age strata: reference is a commit date, not the clock, so two runs over one tree agree
    # byte for byte; --as-of exists to date a historical snapshot, not to move the verdict.
    if as_of is not None:
        reference, reference_source = as_of, "--as-of"
    else:
        reference, reference_source = (commit_date(root, head) if head else None), "HEAD committer date"
    strata = age_strata(findings, tip_dates, reference, reference_source, undated, listed_exactly, census)

    findings.sort(key=lambda f: (CLASSES.index(f["class"]), f["path"], f["oid"]))

    counts = {kind: sum(1 for f in findings if f["class"] == kind) for kind in CLASSES}
    refs = len(rows)
    outside = git_maybe(
        root, "rev-list", "--count", "--all", *extras, *(["--not", head] if head else [])
    )
    reported_worktrees = len(worktrees) if include_worktrees else 1
    return {
        "schema": 3,
        "tool": "git-complement-audit",
        "version": __version__,
        "repo": {
            "head": head,
            "object_format": fmt,
            "refs": refs,
            "worktrees": reported_worktrees,
            "commits_outside_head": int((outside or "0").strip().splitlines()[0] or 0),
        },
        "scope": {
            "all refs except HEAD": True,
            "worktree HEADs": bool(include_worktrees),
            "every path of each ref tip": listed_exactly,
            "ref tips": len(exact_tips),
            "ref tip listing limit": max_tip_listings,
            "unreachable objects": bool(include_unreachable),
            "refs excluded": excluded_refs,
            "ref tips excluded": len(excluded),
            "exclude-ref patterns": list(exclude_refs),
            "excluded secret scan": excluded_scan,
            "history scan": "on" if scan_history else "off",
            "exemption file": exempt_label or "none",
        },
        "hidden_refs": hidden,
        "universe": {
            "paths": len(findings),
            "blobs": len({f["oid"] for f in findings}),
            "bytes": sum(f["size"] for f in findings),
            "unreachable_scanned": unreachable if include_unreachable else 0,
            "blobs_outside_head": len(outside_head),
            "blobs_named": len(outside_head) - len(unnamed),
            "blobs_not_named": len(unnamed),
        },
        "counts": counts,
        "exemptions": exemptions,
        "age_strata": strata,
        "closure": closure,
        "findings": findings,
    }


def has_failure(report: dict) -> bool:
    return any(report["counts"][kind] for kind in FAILING)


def _safe_relative(path: str) -> str:
    parts = [part for part in path.split("/") if part not in ("", ".", "..")]
    return "/".join(parts) or "blob"


def _lock_down(path: pathlib.Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def rescue(repo, report: dict, dest) -> list[dict]:
    """Copy rescuable blobs outside the ODB with 0700/0600 and a verified re-hash."""
    root = pathlib.Path(repo)
    target_root = pathlib.Path(dest).expanduser()
    target_root.mkdir(parents=True, exist_ok=True)
    _lock_down(target_root, 0o700)
    fmt = report["repo"]["object_format"]

    rows = []
    for finding in report["findings"]:
        if finding["class"] not in RESCUABLE:
            continue
        data = git(root, "cat-file", "blob", finding["oid"], text=False)
        if object_id(data, fmt) != finding["oid"]:
            raise ContractError(
                "re-hash mismatch for %s: expected %s, got %s"
                % (finding["path"], finding["oid"], object_id(data, fmt))
            )
        relative = "%s/%s" % (finding["class"], _safe_relative(finding["path"]))
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _lock_down(target, 0o600)
        walked = target.parent
        while walked != target_root.parent:
            _lock_down(walked, 0o700)
            if walked == target_root:
                break
            walked = walked.parent
        rows.append({"path": relative, "oid": finding["oid"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    rows.sort(key=lambda row: row["path"])
    sums = target_root / "SHA256SUMS"
    sums.write_text("".join("%s  %s\n" % (row["sha256"], row["path"]) for row in rows), encoding="utf-8")
    _lock_down(sums, 0o600)
    manifest = {
        "schema": 1,
        "tool": "git-complement-audit",
        "version": __version__,
        "head": report["repo"]["head"],
        "object_format": fmt,
        "classes": list(RESCUABLE),
        "files": rows,
    }
    manifest_path = target_root / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _lock_down(manifest_path, 0o600)
    return rows


def receipt(repo, report: dict, path) -> str:
    """Write a deterministic pre-GC receipt; the same repository yields the same bytes."""
    root = pathlib.Path(repo)
    findings = []
    for finding in report["findings"]:
        data = git(root, "cat-file", "blob", finding["oid"], text=False)
        findings.append(dict(finding, sha256=hashlib.sha256(data).hexdigest()))
    document = {
        "schema": 1,
        "tool": "git-complement-audit",
        "version": __version__,
        "repo": report["repo"],
        "scope": report["scope"],
        "universe": report["universe"],
        "counts": report["counts"],
        "exemptions": report["exemptions"],
        "age_strata": report["age_strata"],
        "closure": report["closure"],
        "findings": findings,
    }
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    destination = pathlib.Path(path).expanduser()
    if destination.parent != pathlib.Path(""):
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render(report: dict) -> str:
    repo, universe, counts = report["repo"], report["universe"], report["counts"]
    closure = report["closure"]
    scope = report["scope"]
    included = [key for key, value in scope.items() if value is True]
    lines = [
        "SCOPE     head=%s object-format=%s refs=%d worktrees=%d commits-outside-head=%d"
        % (
            repo["head"][:12] or "(unborn)",
            repo["object_format"],
            repo["refs"],
            repo["worktrees"],
            repo["commits_outside_head"],
        ),
        "INCLUDED  %s" % "; ".join(included),
    ]
    if scope["exclude-ref patterns"]:
        lines.append(
            "EXCLUDED  patterns=%s refs=%d tips=%d secret-scan=%s"
            % (
                ", ".join(scope["exclude-ref patterns"]),
                scope["refs excluded"],
                scope["ref tips excluded"],
                scope["excluded secret scan"],
            )
        )
    hidden = ["%s%d" % (namespace, count) for namespace, count in report["hidden_refs"].items() if count]
    if hidden:
        lines.append("HIDDEN    runtime namespaces: %s" % " · ".join(hidden))
    exemptions = report.get("exemptions") or {}
    if exemptions.get("declared"):
        lines.append(
            "EXEMPT    %s declared=%d applied=%d unused=%d"
            % (
                exemptions["file"],
                exemptions["declared"],
                exemptions["applied"],
                len(exemptions["unused"]),
            )
        )
        if exemptions["unused"]:
            lines.append(
                "NOTE      %d declared exemption(s) never fired: %s"
                % (
                    len(exemptions["unused"]),
                    ", ".join(oid[:12] for oid in exemptions["unused"][:8]),
                )
            )
    lines += [
        "UNIVERSE  paths-outside-head=%d blobs=%d bytes=%d"
        % (universe["paths"], universe["blobs"], universe["bytes"]),
        "HISTORY   blobs-outside-head=%d named=%d unnamed=%d scan=%s"
        % (
            universe["blobs_outside_head"],
            universe["blobs_named"],
            universe["blobs_not_named"],
            scope["history scan"],
        ),
        "CLOSURE   odb-blobs=%d = head=%d + complement=%d + uncovered=%d"
        % (
            closure["odb_blobs"],
            closure["head_blobs"],
            closure["covered_blobs"],
            closure["uncovered_blobs"],
        ),
        "CLASSES   %s" % " · ".join("%s=%d" % (kind, counts[kind]) for kind in CLASSES),
    ]
    strata = report.get("age_strata") or {}
    if strata.get("state") == "MEASURED" and strata["rows"]:
        lines.append(
            "STRATA    as-of=%s (%s) live<=%dd=%d · recent%d-%dd=%d · stale>%dd=%d"
            " · history-only=%d of %d ref row(s)"
            % (
                strata["as_of"],
                strata["as_of_source"],
                strata["bounds_days"][0],
                strata["live"],
                strata["bounds_days"][0] + 1,
                strata["bounds_days"][1],
                strata["recent"],
                strata["bounds_days"][1],
                strata["stale"],
                strata["history_only"],
                strata["rows"],
            )
        )
    refs = strata.get("refs") or {}
    if strata.get("state") == "MEASURED" and refs.get("total"):
        lines.append(
            "REFS      live<=%dd=%d · recent%d-%dd=%d · stale>%dd=%d of %d ref(s)%s"
            % (
                strata["bounds_days"][0],
                refs["live"],
                strata["bounds_days"][0] + 1,
                strata["bounds_days"][1],
                refs["recent"],
                strata["bounds_days"][1],
                refs["stale"],
                refs["total"],
                " · %d undated" % refs["undated"] if refs["undated"] else "",
            )
        )
    if strata.get("state") == "MEASURED" and strata["undated_refs"]:
        lines.append(
            "NOTE      %d ref(s) carry no committer date and were not dated: %s"
            % (len(strata["undated_refs"]), ", ".join(strata["undated_refs"]))
        )
    if strata.get("state") == "MEASURED" and strata["history_only"]:
        lines.append(
            "NOTE      %d ref row(s) live in no tip tree: their path was last held by a commit "
            "the branches have moved past, so no branch carries them any more"
            % strata["history_only"]
        )
    if strata.get("state") == "MEASURED" and strata["future_dated_rows"]:
        lines.append(
            "NOTE      %d row(s) come from a tip dated after the reference and were counted as "
            "live: the reference is a commit date, not the clock" % strata["future_dated_rows"]
        )
    if closure["uncovered_blobs"] and not scope["unreachable objects"]:
        lines.append(
            "NOTE      %d blob(s) in the object database are reachable from no ref, so no row of "
            "this report holds them; --unreachable names them before 'git gc --prune' deletes "
            "them for good" % closure["uncovered_blobs"]
        )
    elif closure["uncovered_blobs"]:
        lines.append(
            "NOTE      %d blob(s) survived the unreachable pass (%s), so the report is narrower "
            "than the database"
            % (closure["uncovered_blobs"], ", ".join(closure["uncovered_sample"]))
        )
    if not scope["every path of each ref tip"]:
        lines.append(
            "NOTE      per-tip listing skipped: %d ref tips > limit %d, so MOVED can be "
            "under-reported; re-run with --ref-tips %d"
            % (scope["ref tips"], scope["ref tip listing limit"], 2 * scope["ref tips"])
        )
    if scope["excluded secret scan"].startswith("off"):
        lines.append(
            "NOTE      %d excluded ref tips were not scanned for secrets: --allow-excluded-secrets"
            % scope["ref tips excluded"]
        )
    if universe["blobs_not_named"] and scope["history scan"] == "off":
        lines.append(
            "NOTE      %d blob(s) outside HEAD are named by no path row — their path is still in "
            "HEAD, or another blob took it — so their contents were not read; a credential there "
            "survives in every clone; --scan-history reads them" % universe["blobs_not_named"]
        )
    for finding in report["findings"]:
        if finding["class"] == EXEMPT_SECRET:
            origin = "  [%s → declared at %s]" % (finding["declared"], finding["exemption"])
        else:
            origin = "" if finding["root"] == "refs" else "  [%s]" % finding["root"]
        lines.append(
            "  %-18s %9d B  %s%s" % (finding["class"], finding["size"], finding["path"], origin)
        )
    failing = [kind for kind in FAILING if counts[kind]]
    if failing:
        total = sum(counts[kind] for kind in failing)
        lines.append(
            "VERDICT   RC 1 — %d finding(s) outside HEAD need a rescue, a commit or a rotation (%s)"
            % (total, ", ".join("%s=%d" % (kind, counts[kind]) for kind in failing))
        )
    else:
        if universe["blobs_named"]:
            verdict = (
                "VERDICT   RC 0 — no orphaned code or secrets among the %d blob(s) named outside HEAD"
                % universe["blobs_named"]
            )
        else:
            verdict = "VERDICT   RC 0 — no path outside HEAD needs a rescue"
        if exemptions.get("applied"):
            verdict += ", with %d secret claim(s) exempted by %s" % (
                exemptions["applied"],
                exemptions["file"],
            )
        lines.append(verdict)
    return "\n".join(lines)

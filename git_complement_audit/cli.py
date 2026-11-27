"""Command line: one audit, one verdict, three exit codes."""

from __future__ import annotations

import argparse
import datetime
import json
import sys

from . import __version__, engine
from .engine import ContractError
from .selfcheck import run as run_self_test

RC_CLEAN = 0
RC_FINDINGS = 1
RC_CONTRACT = 2


def _as_of(text: str) -> int:
    """Reference date for the age strata: unix seconds, YYYY-MM-DD or ISO 8601.

    A date without a zone is read as UTC, not as local time: the strata must not change
    with the timezone of whoever runs the audit.
    """
    value = (text or "").strip()
    if value.isdigit():
        return int(value)
    moment = None
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            moment = None
    if moment is None:
        raise argparse.ArgumentTypeError(
            "expected unix seconds, YYYY-MM-DD or ISO 8601, got %r" % text
        )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(moment.timestamp())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-complement-audit",
        description=(
            "Audit every path Git keeps outside HEAD — branches, stashes, notes, hidden refs "
            "and detached worktrees — before 'git worktree prune' or 'git gc --prune' "
            "destroys it."
        ),
        epilog=(
            "exit codes: 0 no orphaned code, secrets or history credentials · 1 orphaned code, "
            "secret risk or history credential found · 2 invalid repository or arguments"
        ),
    )
    parser.add_argument("repo", nargs="?", default=".", help="repository to audit (default: .)")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    parser.add_argument("--rescue-dir", metavar="PATH", help="copy rescuable blobs outside the object database")
    parser.add_argument("--receipt", metavar="PATH", help="write a deterministic pre-GC receipt")
    parser.add_argument(
        "--unreachable",
        action="store_true",
        help="also scan objects no ref reaches (slower; finds dropped stashes and dangling blobs)",
    )
    parser.add_argument(
        "--scan-history",
        dest="scan_history",
        action="store_true",
        help=(
            "read the blobs outside HEAD that no path row names — their path still exists in "
            "HEAD, or another blob took it — and report the ones that look secret"
        ),
    )
    parser.add_argument(
        "--no-worktrees",
        dest="worktrees",
        action="store_false",
        help="ignore detached worktree HEADs",
    )
    parser.add_argument(
        "--ref-tips",
        metavar="N",
        type=int,
        default=engine.MAX_TIP_LISTINGS,
        help="list every path of each ref tip while tips <= N (default: %d)" % engine.MAX_TIP_LISTINGS,
    )
    parser.add_argument(
        "--exclude-ref",
        metavar="GLOB",
        dest="exclude_refs",
        action="append",
        default=[],
        help=(
            "leave out refs matching GLOB, e.g. 'refs/agents/*' — repeatable; noise is dropped "
            "but their secret findings are kept and declared in scope"
        ),
    )
    parser.add_argument(
        "--allow-excluded-secrets",
        dest="allow_excluded_secrets",
        action="store_true",
        help="with --exclude-ref, also drop secret findings from those refs (declared in scope)",
    )
    parser.add_argument(
        "--ignore-file",
        metavar="PATH",
        dest="ignore_file",
        help=(
            "exemption file of declared blob ids to exempt from secret gates "
            "(default: .git-complement-audit-ignore at the repository root, if present)"
        ),
    )
    parser.add_argument(
        "--as-of",
        metavar="DATE",
        dest="as_of",
        type=_as_of,
        help=(
            "date the age strata against DATE (unix seconds, YYYY-MM-DD or ISO 8601); "
            "default: the committer date of HEAD, so two runs over one tree agree"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="print nothing unless the contract breaks")
    parser.add_argument("--version", action="version", version="git-complement-audit " + __version__)
    parser.add_argument("--self-test", action="store_true", help="run the synthetic fixture audit and exit")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        report = engine.audit(
            args.repo,
            include_worktrees=args.worktrees,
            include_unreachable=args.unreachable,
            max_tip_listings=args.ref_tips,
            exclude_refs=tuple(args.exclude_refs),
            allow_excluded_secrets=args.allow_excluded_secrets,
            scan_history=args.scan_history,
            ignore_file=args.ignore_file,
            as_of=args.as_of,
        )
        if args.rescue_dir:
            rows = engine.rescue(args.repo, report, args.rescue_dir)
            report["rescue"] = {"dir": str(args.rescue_dir), "written": len(rows)}
        if args.receipt:
            digest = engine.receipt(args.repo, report, args.receipt)
            report["receipt"] = {"path": str(args.receipt), "sha256": digest}
    except ContractError as error:
        print("CONTRACT %s" % error, file=sys.stderr)
        return RC_CONTRACT

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    elif not args.quiet:
        print(engine.render(report))
    return RC_FINDINGS if engine.has_failure(report) else RC_CLEAN

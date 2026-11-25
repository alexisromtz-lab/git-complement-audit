"""Command line: one audit, one verdict, three exit codes."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, engine
from .engine import ContractError
from .selfcheck import run as run_self_test

RC_CLEAN = 0
RC_FINDINGS = 1
RC_CONTRACT = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-complement-audit",
        description=(
            "Audit every path Git keeps outside HEAD — branches, stashes, notes, hidden refs "
            "and detached worktrees — before 'git worktree prune' or 'git gc --prune' "
            "destroys it."
        ),
        epilog=(
            "exit codes: 0 no orphaned code or secrets · 1 orphaned code or secret risk found "
            "· 2 invalid repository or arguments"
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

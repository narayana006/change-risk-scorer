"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from . import __version__
from .config import ConfigError, load_config
from .gitdiff import GitError, changed_files
from .render import RENDERERS
from .scoring import LEVELS, score_change

DEFAULT_CONFIG_NAMES = (".change-risk.yml", ".change-risk.yaml", ".change-risk.json")
EXIT_OK, EXIT_RISKY, EXIT_ERROR = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="change-risk-scorer",
        description="Score the risk of the changes between two git refs and explain the score.",
        epilog="Exit codes: 0 = below --fail-on, 1 = at or above --fail-on, 2 = error.",
    )
    p.add_argument("--repo", default=".", help="path to the git repository (default: current directory)")
    p.add_argument("--base", default="origin/main", help="ref the change will merge into (default: origin/main)")
    p.add_argument("--head", default="HEAD", help="ref being scored (default: HEAD)")
    p.add_argument("--config", help="YAML or JSON config (default: .change-risk.yml in the repo, if present)")
    p.add_argument("-f", "--format", choices=sorted(RENDERERS), default="markdown")
    p.add_argument("-o", "--output", help="also write the report to this file")
    p.add_argument("--fail-on", choices=[*LEVELS[1:], "never"], default="never",
                   help="exit 1 when the level is this or worse (default: never)")
    p.add_argument("--now", help="score as if shipping at this ISO time, e.g. 2026-11-27T16:30 "
                                 "(default: the current time)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def find_config(repo: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for name in DEFAULT_CONFIG_NAMES:
        candidate = os.path.join(repo, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(find_config(args.repo, args.config))
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        files = changed_files(args.repo, args.base, args.head)
    except (ConfigError, GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError:
        print(f"error: --now must be an ISO date/time, got {args.now!r}", file=sys.stderr)
        return EXIT_ERROR

    result = score_change(files, cfg, now)
    report = RENDERERS[args.format](result, args.base, args.head)
    sys.stdout.write(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)

    if args.fail_on != "never" and LEVELS.index(result.level) >= LEVELS.index(args.fail_on):
        return EXIT_RISKY
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

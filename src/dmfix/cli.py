"""Command-line interface.

    dmfix <folder> --deadmesh <dir> [--out <dir>] [--fix crash,heavy,...]
                   [--strength conservative|normal|aggressive] [--no-bsa]

Exit codes: 0 = everything fixable was fixed; 1 = some files failed or were
unfixable; 2 = usage/environment error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dmfix.core.pipeline import CATEGORY_ORDER, PipelineOptions, run_pipeline
from dmfix.core.scanner import DmScanError, FixCategory, find_deadmesh_dir

FIXABLE = [c.value for c in CATEGORY_ORDER]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dmfix",
        description=(
            "DeadMesh Fix Tool - automated collision fixer for meshes flagged by "
            "DeadMesh MOPP Collision Validator. Fixed meshes are written as loose "
            "files to the output folder; originals are never modified."
        ),
    )
    parser.add_argument("folder", help="mod folder to scan (recursively, incl. BSA)")
    parser.add_argument(
        "--deadmesh",
        help="DeadMesh install folder (containing dmscan.exe); auto-detected if omitted",
    )
    parser.add_argument(
        "--out",
        help="output folder for fixed loose files (default: <folder>\\DeadMesh-Fixed)",
    )
    parser.add_argument(
        "--fix",
        default=",".join(FIXABLE),
        help=f"comma-separated fix categories (default: all). Choices: {', '.join(FIXABLE)}",
    )
    parser.add_argument(
        "--strength",
        choices=["conservative", "normal", "aggressive"],
        default="normal",
        help="HEAVY-collision simplification strength (default: normal)",
    )
    parser.add_argument("--no-bsa", action="store_true", help="skip meshes inside BSA archives")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    target = Path(args.folder)
    if not target.is_dir():
        print(f"error: not a folder: {target}", file=sys.stderr)
        return 2

    deadmesh = Path(args.deadmesh) if args.deadmesh else find_deadmesh_dir()
    if deadmesh is None or not (Path(deadmesh) / "dmscan.exe").is_file():
        print(
            "error: DeadMesh folder not found; pass --deadmesh <folder containing dmscan.exe>",
            file=sys.stderr,
        )
        return 2

    try:
        categories = {FixCategory(v.strip()) for v in args.fix.split(",") if v.strip()}
    except ValueError as error:
        print(f"error: unknown fix category ({error}); choices: {', '.join(FIXABLE)}",
              file=sys.stderr)
        return 2

    options = PipelineOptions(
        deadmesh_dir=Path(deadmesh),
        output_dir=Path(args.out) if args.out else target / "DeadMesh-Fixed",
        categories=categories,
        strength=args.strength,
        include_bsa=not args.no_bsa,
    )

    def progress(stage: str, current: int, total: int, message: str) -> None:
        print(f"[{stage} {current + 1}/{total}] {message}", flush=True)

    try:
        report = run_pipeline(target, options, progress)
    except DmScanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print()
    print(report.to_text())
    counts = report.counts()
    return 1 if counts["failed"] or counts["error"] or counts["unfixable"] else 0


if __name__ == "__main__":
    sys.exit(main())

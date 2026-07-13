"""Fix-run report: per-file outcomes plus a whole-run summary.

The pipeline records one FileResult per problem mesh. Nothing lands in the
output folder unless dmscan certified the fix, so `written` doubles as the
"safe to install" flag.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Outcome(Enum):
    FIXED = "fixed"              # verified by dmscan, written to output
    FAILED = "failed"            # fix attempted, verification failed, NOT written
    UNFIXABLE = "unfixable"      # e.g. ORPHAN MOPP: geometry gone, manual work needed
    SKIPPED = "skipped"          # category not selected by the user
    ERROR = "error"              # unexpected exception; details in `reason`


@dataclass
class FileResult:
    source: str                          # path dmscan reported (loose file or BSA member)
    relative_path: str                   # meshes\... path used for the output tree
    categories: list[str]                # fix categories that applied
    outcome: Outcome
    reason: str = ""                     # human-readable failure/skip explanation
    output_path: str = ""                # set when written
    verdict_before: str = ""
    verdict_after: str = ""
    detail: dict = field(default_factory=dict)   # per-fix numbers (tris, rounds, ...)

    @property
    def written(self) -> bool:
        return self.outcome is Outcome.FIXED


@dataclass
class RunReport:
    scanned_folder: str
    output_folder: str
    started: str = ""
    finished: str = ""
    results: list[FileResult] = field(default_factory=list)

    def start(self) -> None:
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def finish(self) -> None:
        self.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def counts(self) -> dict[str, int]:
        counts = {outcome.value: 0 for outcome in Outcome}
        for result in self.results:
            counts[result.outcome.value] += 1
        return counts

    def to_json(self) -> str:
        return json.dumps(
            {
                "tool": "DeadMesh Fix Tool",
                "scanned_folder": self.scanned_folder,
                "output_folder": self.output_folder,
                "started": self.started,
                "finished": self.finished,
                "counts": self.counts(),
                "results": [
                    {
                        "source": r.source,
                        "relative_path": r.relative_path,
                        "categories": r.categories,
                        "outcome": r.outcome.value,
                        "reason": r.reason,
                        "output_path": r.output_path,
                        "verdict_before": r.verdict_before,
                        "verdict_after": r.verdict_after,
                        "detail": r.detail,
                    }
                    for r in self.results
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    def to_text(self) -> str:
        counts = self.counts()
        lines = [
            "DeadMesh Fix Tool - run report",
            f"scanned : {self.scanned_folder}",
            f"output  : {self.output_folder}",
            f"started : {self.started}   finished: {self.finished}",
            (
                f"fixed {counts['fixed']}  failed {counts['failed']}  "
                f"unfixable {counts['unfixable']}  skipped {counts['skipped']}  "
                f"errors {counts['error']}"
            ),
            "",
        ]
        for r in self.results:
            mark = {"fixed": "[OK]", "failed": "[FAIL]", "unfixable": "[MANUAL]",
                    "skipped": "[SKIP]", "error": "[ERR]"}[r.outcome.value]
            lines.append(f"{mark:9s} {r.relative_path}")
            lines.append(f"          {r.verdict_before} -> {r.verdict_after or '-'}"
                         f"  ({', '.join(r.categories) or 'no category'})")
            if r.reason:
                lines.append(f"          {r.reason}")
        if counts["failed"] or counts["unfixable"] or counts["error"]:
            lines += ["", *_failure_guidance(counts)]
        return "\n".join(lines) + "\n"

    def save(self, folder: str | Path) -> tuple[Path, Path]:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        json_path = folder / "deadmesh-fix-report.json"
        text_path = folder / "deadmesh-fix-report.txt"
        json_path.write_text(self.to_json(), encoding="utf-8")
        text_path.write_text(self.to_text(), encoding="utf-8")
        return json_path, text_path


def _failure_guidance(counts: dict[str, int]) -> list[str]:
    """Plain-language 'so what now?' section appended when anything failed."""
    lines = [
        "-" * 68,
        "Some files were NOT fixed - what does that mean, and what can you do?",
        "",
        "First: your game is NOT worse off. This tool never modifies originals,",
        "so a failed file simply keeps behaving exactly as it did before the run.",
        "Note that crash-class defects (CRASH/HANG RISK) rebuild cleanly in",
        "virtually all cases; failures are almost always the performance-class",
        "(HEAVY) meshes, whose symptom is a frame dip on contact - not a crash.",
        "",
    ]
    if counts["failed"]:
        lines += [
            "[FAIL] entries - the fix was attempted but could not be certified",
            "safe by DeadMesh, so it was withheld. Your options, in order:",
            "  1. Re-run with a different simplification strength (try",
            "     'aggressive' in the GUI, or --strength aggressive on the CLI).",
            "  2. Fix the mesh manually - the classic route: import into Blender",
            "     with the PyNifly add-on, rebuild a low-poly collision, export,",
            "     verify with DeadMesh. Step-by-step guide in README.md",
            "     ('Manual fallback').",
            "  3. Leave it. A HEAVY mesh only costs frames when something",
            "     touches it; if it sits somewhere remote, it may never matter.",
            "",
        ]
    if counts["unfixable"]:
        lines += [
            "[MANUAL] entries - the collision geometry was stripped from the",
            "file (ORPHAN MOPP); there is nothing to rebuild from. Either",
            "recreate the collision in Blender (README 'Manual fallback') or",
            "delete the dead collision block in NifSkope.",
            "",
        ]
    if counts["error"]:
        lines += [
            "[ERR] entries - an unexpected error, not a mesh verdict. Re-run",
            "once; if it persists, please report it (attach this file) on the",
            "mod page or the issue tracker.",
            "",
        ]
    return lines

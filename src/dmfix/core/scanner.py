"""Run dmscan.exe (DeadMesh MOPP Collision Validator) and classify its results.

This tool is a downstream companion of DeadMesh: dmscan is the sole authority on
what is broken and on whether a fix worked. We never re-derive verdicts ourselves.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# When the parent is a windowed (console-less) app, every console child would
# otherwise pop up its own console window - one per dmscan call.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FixCategory(Enum):
    """Fix categories the tool can attempt, mapped from dmscan verdicts."""

    CRASH = "crash"            # CRASH RISK / HANG RISK / BROKEN COLLISION -> MOPP rebuild
    HEAVY = "heavy"            # HEAVY / VERY HEAVY COLLISION -> simplify + rebuild
    DEGENERATE = "degenerate"  # DEGENERATE COLLISION -> drop bad tris + rebuild
    INVERTED = "inverted"      # INVERTED COLLISION -> flip winding + rebuild
    ORPHAN_BLOCKS = "orphan_blocks"  # unreferenced collision blocks -> remove
    UNFIXABLE = "unfixable"    # ORPHAN MOPP: geometry stripped, nothing to rebuild from


@dataclass
class ScanRecord:
    """One dmscan JSON record plus the fix categories it maps to."""

    file: str                    # path as reported by dmscan (may point inside a BSA)
    verdict: str
    status: str
    raw: dict
    categories: list[FixCategory] = field(default_factory=list)

    @property
    def needs_fix(self) -> bool:
        return bool(self.categories)


class DmScanError(RuntimeError):
    pass


def classify(record: dict) -> list[FixCategory]:
    """Map one dmscan JSON record to the fix categories it needs."""
    verdict = record.get("verdict", "").upper()
    categories: list[FixCategory] = []

    if record.get("orphan_mopp"):
        categories.append(FixCategory.UNFIXABLE)
        return categories

    # dmscan emits null for analysis sections it did not run on a given mesh.
    broken = record.get("broken") or {}
    freeze = record.get("freeze") or {}
    degenerate = record.get("degenerate") or {}
    orientation = record.get("orientation") or {}

    if broken.get("refs", 0) > 0 or "CRASH" in verdict or "HANG" in verdict \
            or "BROKEN COLLISION" in verdict:
        categories.append(FixCategory.CRASH)

    if freeze.get("cullVerdict", 0) >= 1 or "HEAVY" in verdict:
        categories.append(FixCategory.HEAVY)

    if (degenerate.get("tris") or {}).get("count", 0) > 0 or "DEGENERATE" in verdict:
        categories.append(FixCategory.DEGENERATE)

    if orientation.get("inverted", 0) > 0 or "INVERTED" in verdict:
        categories.append(FixCategory.INVERTED)

    if record.get("orphan_collisions", 0) > 0:
        categories.append(FixCategory.ORPHAN_BLOCKS)

    return categories


class DmScan:
    """Thin wrapper around dmscan.exe."""

    def __init__(self, deadmesh_dir: str | Path) -> None:
        self.deadmesh_dir = Path(deadmesh_dir)
        self.exe = self.deadmesh_dir / "dmscan.exe"
        if not self.exe.is_file():
            raise DmScanError(f"dmscan.exe not found in {self.deadmesh_dir}")

    def _invoke(
        self, args: list[str], timeout: int = 600
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(self.deadmesh_dir),
            creationflags=_NO_WINDOW,
        )

    def _run(self, args: list[str], timeout: int = 600) -> str:
        proc = self._invoke(args, timeout)
        # dmscan uses the exit code as a scan summary: 0 = clean, 1 = problems
        # found. Both carry valid output; only >= 2 is a real failure.
        if proc.returncode not in (0, 1):
            raise DmScanError(
                f"dmscan {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr[:500]}"
            )
        return proc.stdout

    def scan_dir(self, folder: str | Path, include_bsa: bool = True) -> list[ScanRecord]:
        """Scan a folder recursively; one ScanRecord per mesh with collision findings."""
        args = ["--json-dir", str(Path(folder).resolve())]
        if not include_bsa:
            args.append("--no-bsa")
        proc = self._invoke(args, timeout=3600)
        fallback_paths: list[Path] = []
        if proc.returncode not in (0, 1):
            fallback_paths = _cannot_stat_nifs(proc.stderr)
            if not fallback_paths:
                raise DmScanError(
                    f"dmscan {' '.join(args)} failed (exit {proc.returncode}): "
                    f"{proc.stderr[:500]}"
                )

        records = _parse_records(proc.stdout)
        known_files = {str(Path(record.file).resolve()).casefold() for record in records}
        for path in fallback_paths:
            record = self._scan_fallback_file(path)
            normalized = str(Path(record.file).resolve()).casefold()
            if normalized not in known_files:
                records.append(record)
                known_files.add(normalized)
        return records

    def _scan_fallback_file(self, path: Path) -> ScanRecord:
        """Single-scan a directory-mode Unicode failure through an ASCII filename."""
        with tempfile.TemporaryDirectory(prefix="dmfix-scan-") as temp:
            staged = Path(temp) / "input.nif"
            shutil.copyfile(path, staged)
            record = self.scan_file(staged)
        original = str(path.resolve())
        record.file = original
        record.raw["file"] = original
        return record

    def vs_check(self, original: str | Path, rebuilt: str | Path) -> bool:
        """dmscan --vs winding regression gate. True = rebuild is safe.

        Exit codes (verified empirically): 0 = no regression, 3 = the rebuild
        introduced an inversion the original did not have (player would fall
        through), 2 = dmscan could not run the comparison. Fail closed: only
        an explicit 0 counts as safe.
        """
        proc = subprocess.run(
            [str(self.exe), "--vs", str(Path(original).resolve()), str(Path(rebuilt).resolve())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(self.deadmesh_dir),
            creationflags=_NO_WINDOW,
        )
        return proc.returncode == 0

    def scan_file(self, nif_path: str | Path) -> ScanRecord:
        """Scan a single loose .nif (used to verify a fix)."""
        out = self._run(["--json", str(Path(nif_path).resolve())])
        raw = json.loads(out)
        return ScanRecord(
            file=raw.get("file", str(nif_path)),
            verdict=raw.get("verdict", ""),
            status=raw.get("status", ""),
            raw=raw,
            categories=classify(raw),
        )


def _parse_records(out: str) -> list[ScanRecord]:
    records: list[ScanRecord] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(
            ScanRecord(
                file=raw.get("file", ""),
                verdict=raw.get("verdict", ""),
                status=raw.get("status", ""),
                raw=raw,
                categories=classify(raw),
            )
        )
    return records


def _cannot_stat_nifs(stderr: str) -> list[Path]:
    """Return recoverable NIF paths from dmscan's Unicode directory-scan failure."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return []
    paths: list[Path] = []
    prefix = "cannot stat "
    for line in lines:
        if not line.startswith(prefix):
            return []
        path = Path(line[len(prefix) :])
        if path.suffix.casefold() != ".nif" or not path.is_file():
            return []
        paths.append(path)
    return paths


def find_deadmesh_dir(candidates: list[str | Path] | None = None) -> Path | None:
    """Best-effort auto-detection of the DeadMesh install folder."""
    default_candidates: list[Path] = []
    try:
        default_candidates.append(
            Path(__file__).resolve().parents[4].parent
            / "DeadMesh - MOPP Collision Validator"
        )
    except IndexError:  # path too shallow (e.g. frozen build layout)
        pass
    for cand in [*(candidates or []), *default_candidates]:
        cand = Path(cand)
        if (cand / "dmscan.exe").is_file():
            return cand
    return None

"""End-to-end fix pipeline: scan -> extract -> fix -> verify -> output.

Policy (agreed with the user): never modify originals; never repack BSA; a fix
is only written to the output folder when dmscan certifies both that the
targeted defect is gone and that nothing else got worse. Files that cannot be
certified fail closed and are listed in the report instead.
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dmfix.core.bsa import BsaArchive
from dmfix.core.report import FileResult, Outcome, RunReport
from dmfix.core.scanner import DmScan, FixCategory, ScanRecord

ProgressCallback = Callable[[str, int, int, str], None]

# Order matters: structural crash repair first, then geometry cleanups, then
# the (geometry-changing) simplification, orphan-block removal last.
CATEGORY_ORDER = [
    FixCategory.CRASH,
    FixCategory.DEGENERATE,
    FixCategory.INVERTED,
    FixCategory.HEAVY,
    FixCategory.ORPHAN_BLOCKS,
]


@dataclass
class PipelineOptions:
    deadmesh_dir: Path
    output_dir: Path
    categories: set[FixCategory] = field(
        default_factory=lambda: set(CATEGORY_ORDER)
    )
    strength: str = "normal"          # HEAVY simplification strength
    include_bsa: bool = True


@dataclass
class WorkItem:
    relative_path: str                # meshes\... (windows separators, lowercase)
    source_kind: str                  # "loose" | "bsa"
    source_path: Path                 # loose file, or the .bsa archive
    bsa_inner_path: str = ""          # set when source_kind == "bsa"
    record: ScanRecord | None = None


def _fix_functions():
    """Category -> fix callable, imported lazily so missing stages degrade cleanly.

    Each callable has signature (input_path, output_path, **kwargs) and returns
    an object with a truthy `.success` (or raises). Fixes verify themselves via
    dmscan before reporting success.
    """
    from dmfix.core.fixes.mopp_rebuild import rebuild_mopp
    from dmfix.core.fixes.simplify import simplify_collision

    functions: dict[FixCategory, Callable] = {
        FixCategory.CRASH: lambda src, dst, **kw: rebuild_mopp(src, dst),
        FixCategory.HEAVY: lambda src, dst, **kw: simplify_collision(
            src, dst,
            strength=kw.get("strength", "normal"),
            deadmesh_dir=kw.get("deadmesh_dir"),
        ),
    }
    try:
        from dmfix.core.fixes.degenerate import fix_degenerate
        functions[FixCategory.DEGENERATE] = lambda src, dst, **kw: fix_degenerate(
            src, dst, deadmesh_dir=kw.get("deadmesh_dir")
        )
    except ImportError:
        pass
    try:
        from dmfix.core.fixes.winding import fix_inverted
        functions[FixCategory.INVERTED] = lambda src, dst, **kw: fix_inverted(
            src, dst, deadmesh_dir=kw.get("deadmesh_dir")
        )
    except ImportError:
        pass
    try:
        from dmfix.core.fixes.orphan import remove_orphan_collision
        functions[FixCategory.ORPHAN_BLOCKS] = lambda src, dst, **kw: remove_orphan_collision(src, dst)
    except ImportError:
        pass
    return functions


def _relative_mesh_path(file_path: str) -> str:
    """Extract the meshes\\... suffix from a dmscan-reported absolute path."""
    normalized = file_path.replace("/", "\\").lower()
    marker = "\\meshes\\"
    index = normalized.rfind(marker)
    if index < 0:
        if normalized.startswith("meshes\\"):
            return normalized
        return Path(normalized).name
    return normalized[index + 1 :]


def _bsa_precedence(bsa_path: Path) -> tuple[int, str]:
    """Sort key approximating Skyrim archive precedence for same-path conflicts.

    A plugin's BSA loads with the plugin; esp loads after (overrides) esm. We
    match the BSA to a sibling plugin by name prefix. Loose files override all
    BSAs, which the pipeline handles separately.
    """
    stem = bsa_path.stem.lower()
    # "Midnight Sun - Update" matches "Midnight Sun - Update.esp"
    for suffix, rank in ((".esp", 2), (".esl", 1), (".esm", 0)):
        if (bsa_path.parent / f"{stem}{suffix}").exists():
            return (rank, stem)
        # textures/meshes split archives: "Foo - Textures.bsa" belongs to Foo.esm
        base = stem.split(" - ")[0]
        if (bsa_path.parent / f"{base}{suffix}").exists():
            return (rank, stem)
    return (0, stem)


def collect_work_items(
    target_folder: Path,
    options: PipelineOptions,
    progress: ProgressCallback,
) -> tuple[list[WorkItem], Path]:
    """Scan the target folder (loose + BSA members) and build the fix worklist.

    Returns (items needing a fix, temp dir holding extracted BSA members).
    The caller owns the returned temp dir; on exception it is already removed.
    """
    scanner = DmScan(options.deadmesh_dir)
    temp_root = Path(tempfile.mkdtemp(prefix="dmfix-bsa-"))
    try:
        return _collect_work_items(target_folder, options, progress, scanner, temp_root)
    except BaseException:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def _collect_work_items(
    target_folder: Path,
    options: PipelineOptions,
    progress: ProgressCallback,
    scanner: DmScan,
    temp_root: Path,
) -> tuple[list[WorkItem], Path]:
    progress("scan", 0, 1, str(target_folder))
    loose_records = scanner.scan_dir(target_folder)

    # winner per relative path: loose always beats BSA; higher-precedence BSA
    # beats lower.
    items: dict[str, WorkItem] = {}
    for record in loose_records:
        rel = _relative_mesh_path(record.file)
        items[rel] = WorkItem(
            relative_path=rel,
            source_kind="loose",
            source_path=Path(record.file),
            record=record,
        )

    if options.include_bsa:
        bsa_files = sorted(
            (p for p in Path(target_folder).rglob("*.bsa")),
            key=_bsa_precedence,
        )
        for bsa_index, bsa_path in enumerate(bsa_files):
            progress("extract", bsa_index, len(bsa_files), bsa_path.name)
            extract_dir = temp_root / bsa_path.stem
            try:
                with BsaArchive(bsa_path) as archive:
                    nif_names = [n for n in archive.namelist() if n.endswith(".nif")]
                    for name in nif_names:
                        dest = extract_dir / Path(name.replace("/", "\\"))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(archive.read(name))
            except ValueError:
                continue  # unreadable/unsupported archive: skip, loose scan still stands
            if not extract_dir.is_dir():
                continue
            progress("scan", bsa_index, len(bsa_files), f"{bsa_path.name} (extracted)")
            for record in scanner.scan_dir(extract_dir):
                rel = _relative_mesh_path(record.file)
                existing = items.get(rel)
                if existing and existing.source_kind == "loose":
                    continue  # loose wins at runtime; the BSA copy is never loaded
                # among BSAs, later in precedence-sorted order wins
                items[rel] = WorkItem(
                    relative_path=rel,
                    source_kind="bsa",
                    source_path=bsa_path,
                    bsa_inner_path=rel.replace("\\", "/"),
                    record=record,
                )

    worklist = [item for item in items.values() if item.record and item.record.needs_fix]
    worklist.sort(key=lambda item: item.relative_path)
    return worklist, temp_root


def run_pipeline(
    target_folder: str | Path,
    options: PipelineOptions,
    progress: ProgressCallback | None = None,
) -> RunReport:
    progress = progress or (lambda *_: None)
    target_folder = Path(target_folder)
    scanner = DmScan(options.deadmesh_dir)
    fixes = _fix_functions()

    report = RunReport(
        scanned_folder=str(target_folder),
        output_folder=str(options.output_dir),
    )
    report.start()

    worklist, temp_root = collect_work_items(target_folder, options, progress)
    work_root = Path(tempfile.mkdtemp(prefix="dmfix-work-"))
    try:
        for index, item in enumerate(worklist):
            record = item.record
            assert record is not None
            progress("fix", index, len(worklist), item.relative_path)
            result = _process_item(item, record, options, fixes, scanner, work_root)
            report.results.append(result)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        shutil.rmtree(work_root, ignore_errors=True)

    report.finish()
    report.save(options.output_dir)
    return report


def _process_item(
    item: WorkItem,
    record: ScanRecord,
    options: PipelineOptions,
    fixes: dict[FixCategory, Callable],
    scanner: DmScan,
    work_root: Path,
) -> FileResult:
    categories = [c for c in CATEGORY_ORDER if c in record.categories]
    base = FileResult(
        source=(
            f"{item.source_path}::{item.bsa_inner_path}"
            if item.source_kind == "bsa"
            else str(item.source_path)
        ),
        relative_path=item.relative_path,
        categories=[c.value for c in categories],
        outcome=Outcome.ERROR,
        verdict_before=record.verdict,
    )

    if FixCategory.UNFIXABLE in record.categories:
        base.outcome = Outcome.UNFIXABLE
        base.reason = (
            "ORPHAN MOPP: the collision tree exists but its geometry was stripped; "
            "there is nothing to rebuild from. Recreate the collision manually "
            "(Blender + PyNifly) or remove it in NifSkope."
        )
        return base

    selected = [c for c in categories if c in options.categories]
    if not selected:
        base.outcome = Outcome.SKIPPED
        base.reason = "all applicable fix categories are deselected"
        return base
    missing = [c.value for c in selected if c not in fixes]
    if missing:
        base.outcome = Outcome.ERROR
        base.reason = f"fix not implemented: {', '.join(missing)}"
        return base

    # Stage the source bytes as a loose work file.
    work_dir = work_root / f"{abs(hash(item.relative_path)):x}"
    work_dir.mkdir(parents=True, exist_ok=True)
    current = work_dir / Path(item.relative_path).name
    original = work_dir / f"original_{Path(item.relative_path).name}"
    try:
        if item.source_kind == "bsa":
            with BsaArchive(item.source_path) as archive:
                data = archive.read(item.bsa_inner_path)
            original.write_bytes(data)
        else:
            shutil.copyfile(item.source_path, original)
        shutil.copyfile(original, current)

        detail: dict = {}
        for category in selected:
            step_out = work_dir / f"{category.value}_{current.name}"
            result = fixes[category](
                current,
                step_out,
                strength=options.strength,
                deadmesh_dir=options.deadmesh_dir,
            )
            if not getattr(result, "success", True):
                base.outcome = Outcome.FAILED
                base.reason = (
                    f"{category.value}: fix could not be certified "
                    f"({getattr(result, 'verdict', 'see detail')})"
                )
                base.detail = {**detail, category.value: _result_detail(result)}
                return base
            detail[category.value] = _result_detail(result)
            current = step_out

        final = scanner.scan_file(current)
        base.verdict_after = final.verdict
        remaining = [c for c in final.categories if c in selected]
        if remaining:
            base.outcome = Outcome.FAILED
            base.reason = (
                "final dmscan verification still reports: "
                + ", ".join(c.value for c in remaining)
            )
            base.detail = detail
            return base
        if FixCategory.CRASH in final.categories:
            base.outcome = Outcome.FAILED
            base.reason = "fix introduced a crash-class defect; output withheld"
            base.detail = detail
            return base
        if not scanner.vs_check(original, current):
            base.outcome = Outcome.FAILED
            base.reason = (
                "dmscan --vs: rebuild introduced an inverted-winding regression; "
                "output withheld"
            )
            base.detail = detail
            return base

        destination = options.output_dir / Path(item.relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, destination)
        base.outcome = Outcome.FIXED
        base.output_path = str(destination)
        base.detail = detail
        return base
    except Exception as error:  # fail closed per file, keep the run going
        base.outcome = Outcome.ERROR
        base.reason = f"{type(error).__name__}: {error}"
        return base
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _result_detail(result) -> dict:
    detail = {}
    for name in (
        "rounds",
        "old_triangle_count",
        "new_triangle_count",
        "old_cull_worst",
        "new_cull_worst",
        "verdict",
        "triangle_count",
        "old_block_size",
        "new_block_size",
    ):
        value = getattr(result, name, None)
        if value is not None:
            detail[name] = value
    return detail

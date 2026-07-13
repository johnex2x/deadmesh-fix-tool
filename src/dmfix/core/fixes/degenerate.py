from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dmfix.core.fixes._mesh_rewrite import write_collision_mesh
from dmfix.core.fixes.acceptance import nothing_got_worse
from dmfix.core.fixes.mopp_rebuild import decode_compressed_mesh
from dmfix.core.fixes.simplify import _drop_degenerate
from dmfix.core.nif_io import NifFileLayout, locate_collisions
from dmfix.core.scanner import DmScan, DmScanError, find_deadmesh_dir


@dataclass(frozen=True)
class DegenerateResult:
    success: bool
    output_path: Path
    reason: str
    old_triangle_count: int
    new_triangle_count: int
    baseline_scan: dict | None
    scan: dict | None


def fix_degenerate(
    input_path: str | Path,
    output_path: str | Path,
    deadmesh_dir: str | Path | None = None,
) -> DegenerateResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    baseline: dict | None = None
    old_count = 0
    new_count = 0
    try:
        scanner = _scanner(deadmesh_dir)
        baseline = scanner.scan_file(input_path).raw
        layout = NifFileLayout.read(input_path)
        collisions = locate_collisions(input_path)
        if len(collisions) != 1:
            raise ValueError(f"expected exactly one MOPP collision, found {len(collisions)}")
        collision = collisions[0]
        if collision.shape_chain[1] != "bhkCompressedMeshShape":
            raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")
        mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
        old_count = len(mesh.triangles)
        vertices, triangles, materials = _drop_degenerate(mesh)
        if not triangles:
            raise ValueError("collision mesh has no non-degenerate triangles")
        if len(triangles) == old_count:
            raise ValueError("decoded collision contains no degenerate triangles")
        new_count = write_collision_mesh(
            layout, collision, mesh, vertices, triangles, materials, output_path
        )
        scan = scanner.scan_file(output_path).raw
        accepted = (
            scan["degenerate"]["tris"]["count"] == 0
            and nothing_got_worse(
                baseline, scan, ignore=frozenset({"degenerate"})
            )
        )
        if not accepted:
            output_path.unlink(missing_ok=True)
            return DegenerateResult(
                False,
                output_path,
                "dmscan verification failed or another defect got worse",
                old_count,
                new_count,
                baseline,
                scan,
            )
        return DegenerateResult(
            True,
            output_path,
            f"removed {old_count - new_count} degenerate triangles",
            old_count,
            new_count,
            baseline,
            scan,
        )
    except (ValueError, OSError, DmScanError) as exc:
        output_path.unlink(missing_ok=True)
        return DegenerateResult(
            False, output_path, str(exc), old_count, new_count, baseline, None
        )


def _scanner(deadmesh_dir: str | Path | None = None) -> DmScan:
    """DmScan from an explicit DeadMesh folder, or repo-relative discovery.

    Callers with a configured DeadMesh location (pipeline/GUI/CLI) must pass it
    explicitly — the discovery fallback only works from a source checkout and
    fails in a frozen build.
    """
    if deadmesh_dir is not None:
        return DmScan(deadmesh_dir)
    checkout = Path(__file__).resolve().parents[4].parent / "DeadMesh - MOPP Collision Validator"
    scanner_dir = find_deadmesh_dir([checkout])
    if scanner_dir is None:
        raise ValueError("dmscan.exe could not be located")
    return DmScan(scanner_dir)

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dmfix.core.fixes.mopp_rebuild import (
    _compile_verified_mopp,
    _serialize_mopp,
)
from dmfix.core.fixes.acceptance import nothing_got_worse
from dmfix.core.fixes.mopp_rebuild import decode_compressed_mesh
from dmfix.core.fixes.simplify import _encode_compressed_mesh
from dmfix.core.fixes.simplify import _drop_degenerate
from dmfix.core.nif_io import NifFileLayout, locate_collisions, read_mopp
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
    strength: str = "conservative",
    stop_check=None,
    item_progress=None,
) -> DegenerateResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    baseline: dict | None = None
    old_count = 0
    new_count = 0
    stop_check = stop_check or (lambda: None)
    item_progress = item_progress or (lambda _message: None)
    try:
        scanner = _scanner(deadmesh_dir)
        baseline = scanner.scan_file(input_path).raw
        layout = NifFileLayout.read(input_path)
        collisions = locate_collisions(input_path)
        if len(collisions) != 1 and strength == "conservative":
            raise ValueError(
                "UNSUPPORTED_MULTI_MOPP: degenerate cleanup requires one "
                f"independent collision group; found {len(collisions)}"
            )
        replacements: dict[int, bytes] = {}
        changed_groups = 0
        for collision in collisions:
            stop_check()
            if collision.shape_chain[1] != "bhkCompressedMeshShape":
                raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")
            mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
            old_count += len(mesh.triangles)
            vertices, triangles, materials = _drop_degenerate(
                mesh,
                geometric=(strength != "conservative" and len(collisions) > 1),
            )
            if len(triangles) == len(mesh.triangles):
                continue
            if not triangles:
                raise ValueError("collision mesh has no non-degenerate triangles")
            changed_groups += 1
            new_count += len(triangles)
            encoded = _encode_compressed_mesh(mesh, vertices, triangles, materials)
            code, origin, scale, _, _ = _compile_verified_mopp(
                encoded.vertices,
                encoded.triangles,
                encoded.output_ids,
                mesh.radius,
            )
            replacements[mesh.data_block_index] = encoded.payload
            replacements[collision.shape_block_index] = _serialize_mopp(
                read_mopp(layout, collision.shape_block_index), code, origin, scale
            )
        if not replacements:
            if len(collisions) > 1 and int(
                ((baseline.get("degenerate") or {}).get("tris") or {}).get("count", 0)
            ) > 0:
                raise ValueError(
                    "UNSUPPORTED_MULTI_MOPP: dmscan degenerate findings are not "
                    "represented by decodable collision triangles"
                )
            raise ValueError("decoded collision contains no degenerate triangles")
        item_progress("writing degenerate collision cleanup")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(layout.replace_blocks(replacements))
        stop_check()
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
            f"removed {old_count - new_count} degenerate triangles from {changed_groups} collision group(s)",
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
    try:
        checkout = (
            Path(__file__).resolve().parents[4].parent
            / "DeadMesh - MOPP Collision Validator"
        )
        scanner_dir = find_deadmesh_dir([checkout])
    except IndexError:  # path too shallow (e.g. frozen build layout)
        scanner_dir = None
    if scanner_dir is None:
        raise ValueError(
            "dmscan.exe could not be located; configure the DeadMesh folder"
        )
    return DmScan(scanner_dir)

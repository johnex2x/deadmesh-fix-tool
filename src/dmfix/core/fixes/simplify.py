from __future__ import annotations

import math
import hashlib
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fast_simplification
import numpy as np

from dmfix.core.fixes.mopp_rebuild import (
    CollisionMesh,
    _compile_verified_mopp,
    _serialize_mopp,
    decode_compressed_mesh,
)
from dmfix.core.fixes.acceptance import (
    safety_certification_failures,
    simplify_certification_failures,
    simplify_scan_is_acceptable,
)
from dmfix.core.nif_io import CollisionInfo, NifFileLayout, locate_collisions, read_mopp


MAX_QUANTIZED_EXTENT = 65.535
HAVOK_TO_SKYRIM = 69.99125
STRENGTH_RATIOS = {"conservative": 0.5, "normal": 0.25, "aggressive": 0.15}


@dataclass(frozen=True)
class SimplifyResult:
    success: bool
    output_path: Path
    rounds: int
    old_triangle_count: int
    new_triangle_count: int
    old_cull_worst: int
    new_cull_worst: int | None
    cull_verdict: int | None
    verdict: str
    verifier_passed: bool
    welding_arrays_empty: bool
    tolerance_used: str
    certification_failures: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class _EncodedMesh:
    payload: bytes
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    output_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ComponentProvenance:
    key: tuple[tuple[float, float, float], ...]
    points: np.ndarray
    faces: np.ndarray
    source_points: np.ndarray
    source_faces: np.ndarray


def _world_to_havok(point: np.ndarray, collision: CollisionInfo) -> np.ndarray:
    """Invert the NIF target-node/body transform used by dmscan coordinates."""
    target_linear = np.asarray(collision.target_linear, dtype=np.float64).reshape(3, 3)
    target_translation = np.asarray(collision.target_translation, dtype=np.float64)
    body_translation = np.asarray(collision.body_translation, dtype=np.float64)
    x, y, z, w = collision.body_rotation
    body_rotation = np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    havok_scaled = np.linalg.solve(target_linear, np.asarray(point, dtype=np.float64) - target_translation)
    return body_rotation.T @ (havok_scaled / HAVOK_TO_SKYRIM - body_translation)


def _rescue_budget(strength: str) -> int:
    return {"conservative": 0, "normal": 8, "aggressive": 8}.get(strength, 0)


def _has_safety_defects(scan: dict | None, baseline: dict | None = None) -> bool:
    if not scan:
        return False
    if baseline is not None:
        # Ambiguous winding is a certification signal, but dmscan does not
        # provide a reliable point for it. It must not pin a component-local
        # rescue target after the actual inverted faces are already repaired.
        return any(
            failure
            for failure in safety_certification_failures(baseline, scan)
            if not failure.startswith("winding_cull.ambiguous=")
        )
    return any(
        (
            int((scan.get("broken") or {}).get("refs", 0)) > 0,
            int((scan.get("orientation") or {}).get("inverted", 0)) > 0,
            int((scan.get("winding_cull") or {}).get("inverted", 0)) > 0,
            int(((scan.get("degenerate") or {}).get("tris") or {}).get("count", 0)) > 0,
            str(scan.get("ray_status", "ok")).casefold() != "ok",
            str((scan.get("fall_through_risk") or {}).get("level", "none")).casefold()
            != "none",
            int((scan.get("invisible_walls") or {}).get("count", 0)) > 0,
        )
    )


def _rescue_target(
    initial_target: int,
    reduction_index: int,
    component_count: int,
    *,
    local_only: bool,
    current_target: int | None = None,
    gentle: bool = False,
) -> int:
    """Protect multi-component safety repairs from destructive global reduction."""
    if local_only and component_count > 3:
        return current_target if current_target is not None else initial_target
    if gentle and component_count > 3:
        return max(200, math.floor(initial_target * (0.75**reduction_index)))
    return max(200, initial_target // (2**reduction_index))


def _local_reserve(component_count: int, largest_source_faces: int) -> int:
    """Match reserve size to the component topology, not only its count."""
    if component_count > 10 and largest_source_faces > 5_000:
        return 512
    if component_count <= 3 and largest_source_faces > 10_000:
        return 64
    return 8


def _repair_closed_component_winding(
    points: np.ndarray,
    faces: np.ndarray,
    source_points: np.ndarray,
    source_faces: np.ndarray,
) -> np.ndarray:
    if len(faces) < 4 or len(source_faces) < 4:
        return faces
    candidate = points[faces]
    source = source_points[source_faces]
    candidate_volume = float(
        np.einsum("ij,ij->i", candidate[:, 0], np.cross(candidate[:, 1], candidate[:, 2])).sum()
        / 6.0
    )
    source_volume = float(
        np.einsum("ij,ij->i", source[:, 0], np.cross(source[:, 1], source[:, 2])).sum()
        / 6.0
    )
    if source_volume and candidate_volume and (source_volume > 0) != (candidate_volume > 0):
        return faces[:, [0, 2, 1]]
    return faces


def _defect_component_overrides(
    scan: dict,
    provenance: tuple[_ComponentProvenance, ...],
    collision: CollisionInfo,
    strength: str,
) -> dict[tuple[tuple[float, float, float], ...], tuple[int, float]]:
    """Map dmscan defect points to components for a safer retry."""
    defects: list[np.ndarray] = []
    def add_point(value: object) -> None:
        if not isinstance(value, list) or len(value) != 3:
            return
        point = np.asarray(value, dtype=np.float64)
        if np.allclose(point, 0.0):
            return
        defects.append(_world_to_havok(point, collision))

    orientation = scan.get("orientation") or {}
    for component in orientation.get("bad_components") or []:
        add_point(component.get("center"))
    invisible = scan.get("invisible_walls") or {}
    for point in invisible.get("pts") or []:
        add_point(point)
    winding = scan.get("winding_cull") or {}
    winding_points = winding.get("at") or winding.get("pts") or []
    if isinstance(winding_points, dict):
        winding_points = list(winding_points.values())
    for point in winding_points:
        add_point(point)
    overrides: dict[tuple[tuple[float, float, float], ...], tuple[int, float]] = {}
    # A single very large, open wall needs a wider local reserve after the
    # second decimation pass; smaller/open bridge islands are safer with the
    # tight reserve above the current result.
    reserve = _local_reserve(
        len(provenance),
        max((len(component.source_faces) for component in provenance), default=0),
    )
    for defect in defects:
        ranked: list[tuple[float, _ComponentProvenance]] = []
        for component in provenance:
            if not len(component.faces):
                continue
            centroids = component.points[component.faces].mean(axis=1)
            distance = float(np.min(np.sum((centroids - defect) ** 2, axis=1)))
            ranked.append((distance, component))
        if not ranked:
            continue
        component = min(ranked, key=lambda item: item[0])[1]
        current = len(component.faces)
        source = len(component.source_faces)
        # A small geometry reserve is enough to remove the local defect. A
        # large reserve pushes the whole bridge back over the HEAVY threshold;
        # keep the retry bounded and let the decimator do moderate cleanup.
        target = min(source, current + reserve)
        overrides[component.key] = (
            target,
            8.0 if strength in {"normal", "aggressive"} else 6.0,
        )
    return overrides


def simplify_collision(
    input_path: str | Path,
    output_path: str | Path,
    strength: str = "normal",
    deadmesh_dir: str | Path | None = None,
    stop_check: Callable[[], None] | None = None,
    item_progress: Callable[[str], None] | None = None,
) -> SimplifyResult:
    stop_check = stop_check or (lambda: None)
    item_progress = item_progress or (lambda _message: None)
    stop_check()
    if strength not in STRENGTH_RATIOS:
        raise ValueError(f"unknown simplification strength {strength!r}")

    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    item_progress("analyzing collision")
    layout = NifFileLayout.read(input_path)
    collisions = locate_collisions(input_path)
    if len(collisions) != 1:
        if strength != "conservative":
            return _simplify_multi_collision(
                input_path,
                output_path,
                strength,
                deadmesh_dir,
                stop_check,
                item_progress,
            )
        raise ValueError(
            "UNSUPPORTED_MULTI_MOPP: heavy simplification requires one independent "
            f"collision group; found {len(collisions)}"
        )
    collision = collisions[0]
    if collision.shape_chain[1] != "bhkCompressedMeshShape":
        raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")

    shape_payload = layout.payload(collision.child_shape_block_index)
    old_mopp = read_mopp(layout, collision.shape_block_index)
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    vertices, triangles, materials = _drop_degenerate(mesh)
    if not triangles:
        raise ValueError("collision mesh has no non-degenerate triangles")

    # Explicit DeadMesh location from the caller (pipeline/GUI/CLI), with a
    # source-checkout discovery fallback for the test suite.
    from dmfix.core.fixes.degenerate import _scanner

    scanner = _scanner(deadmesh_dir)
    item_progress("baseline DeadMesh scan")
    stop_check()
    baseline = scanner.scan_file(input_path).raw
    stop_check()
    old_cull_worst = int(baseline["freeze"]["cullWorst"])
    initial_target = min(
        max(200, math.floor(len(triangles) * STRENGTH_RATIOS[strength])),
        1500,
    )

    last_scan: dict | None = None
    last_triangle_count = 0
    last_round = 0
    item_progress("analyzing collision components")
    stop_check()
    collision_components = _connected_face_components(
        list(range(len(triangles))), vertices, triangles
    )
    component_count = len(collision_components)
    topology_reserve = _local_reserve(
        component_count,
        max((len(component) for component in collision_components), default=0),
    )
    for round_number in range(1, 5):
        stop_check()
        if round_number == 4:
            if (
                component_count <= 100
                or last_scan is None
                or "HEAVY" not in last_scan["verdict"].upper()
            ):
                break
        target_round = min(round_number, 3)
        target = max(1, initial_target // (2 ** (target_round - 1)))
        # Try pure decimation first: hulls rescue many-island meshes but can
        # add invisible walls over concave islands, so they are the fallback,
        # not the default. Round 4 exists only for many-island meshes, where
        # pure decimation has already failed three times.
        if round_number == 4:
            hull_variants: tuple[int, ...] = (64,)
        else:
            hull_variants = (0, 16)
        for hull_threshold in hull_variants:
            variant = "decimation" if hull_threshold == 0 else "hull fallback"
            item_progress(
                f"simplification round {round_number}/4: {variant}"
            )
            stop_check()
            new_vertices, new_triangles, new_materials = _simplify_by_material(
                vertices,
                triangles,
                materials,
                target,
                component_floor=(8, 2, 1)[target_round - 1],
                aggressiveness=(6.0, 8.0, 10.0)[target_round - 1],
                hull_threshold=hull_threshold,
            )
            stop_check()
            encoded = _encode_compressed_mesh(mesh, new_vertices, new_triangles, new_materials)
            last_triangle_count = len(encoded.triangles)

            item_progress(
                f"simplification round {round_number}/4: MOPP rebuild"
            )
            stop_check()
            code, origin, scale, _, _ = _compile_verified_mopp(
                encoded.vertices,
                encoded.triangles,
                encoded.output_ids,
                mesh.radius,
            )
            stop_check()
            mopp_payload = _serialize_mopp(old_mopp, code, origin, scale)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                layout.replace_blocks(
                    {
                        mesh.data_block_index: encoded.payload,
                        collision.shape_block_index: mopp_payload,
                    }
                )
            )
            output_layout = NifFileLayout.read(output_path)
            if output_layout.payload(collision.child_shape_block_index) != shape_payload:
                raise AssertionError("bhkCompressedMeshShape payload changed")

            item_progress(
                f"simplification round {round_number}/4: DeadMesh scan"
            )
            stop_check()
            last_scan = scanner.scan_file(output_path).raw
            stop_check()
            last_round = round_number
            if simplify_scan_is_acceptable(baseline, last_scan):
                return SimplifyResult(
                    success=True,
                    output_path=output_path,
                    rounds=round_number,
                    old_triangle_count=len(mesh.triangles),
                    new_triangle_count=last_triangle_count,
                    old_cull_worst=old_cull_worst,
                    new_cull_worst=int(last_scan["freeze"]["cullWorst"]),
                    cull_verdict=int(last_scan["freeze"]["cullVerdict"]),
                    verdict=last_scan["verdict"],
                    verifier_passed=True,
                    welding_arrays_empty=True,
                    tolerance_used=_tolerance_description(baseline, last_scan),
                    certification_failures=(),
                )

    rescue_budget = _rescue_budget(strength)
    component_overrides: dict[
        tuple[tuple[float, float, float], ...], tuple[int, float]
    ] = {}
    local_only = component_count > 3 and _has_safety_defects(last_scan, baseline)
    gentle_reduction = local_only
    reduction_index = 0
    rescue_initial_target = 1500 if topology_reserve == 8 else initial_target
    target = rescue_initial_target
    previous_progress: tuple[int, ...] | None = None
    stalled_candidates = 0
    for rescue_index in range(rescue_budget):
        stop_check()
        target = _rescue_target(
            rescue_initial_target,
            reduction_index,
            component_count,
            local_only=local_only,
            current_target=target,
            gentle=gentle_reduction,
        )
        if strength == "aggressive" and component_count <= 3 and rescue_index >= 2:
            target = min(target, 128)
        repeat_passes = (
            2 if strength == "normal" or topology_reserve == 8 else 3
        )
        item_progress(
            f"targeted rescue {rescue_index + 1}/{rescue_budget}: "
            f"target {target}"
        )
        # On a low-component mesh, a medium isolated island can reject every
        # decimation attempt while keeping the whole MOPP over the HEAVY gate.
        # Hull only that bounded island on later candidates; multi-island
        # bridges and towers must retain their concave local topology.
        rescue_hull_threshold = (
            256 if component_count <= 3 and rescue_index > 0 else 0
        )
        rescued = _simplify_by_material(
            vertices,
            triangles,
            materials,
            target,
            component_floor=1,
            aggressiveness=(
                8.0
                if strength == "normal" or topology_reserve == 8
                else (20.0 if component_count <= 3 else 10.0)
            ),
            hull_threshold=rescue_hull_threshold,
            component_overrides=component_overrides,
            repair_closed_winding=True,
            return_provenance=True,
            repeat_passes=repeat_passes,
            split_planar_patches=(
                strength == "aggressive"
                and component_count <= 3
                and rescue_index >= 2
            ),
        )
        new_vertices, new_triangles, new_materials, provenance = rescued
        encoded = _encode_compressed_mesh(mesh, new_vertices, new_triangles, new_materials)
        last_triangle_count = len(encoded.triangles)
        stop_check()
        code, origin, scale, _, _ = _compile_verified_mopp(
            encoded.vertices,
            encoded.triangles,
            encoded.output_ids,
            mesh.radius,
        )
        mopp_payload = _serialize_mopp(old_mopp, code, origin, scale)
        candidate_bytes = layout.replace_blocks(
            {
                mesh.data_block_index: encoded.payload,
                collision.shape_block_index: mopp_payload,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(candidate_bytes)
        output_layout = NifFileLayout.read(output_path)
        if output_layout.payload(collision.child_shape_block_index) != shape_payload:
            raise AssertionError("bhkCompressedMeshShape payload changed")
        stop_check()
        last_scan = scanner.scan_file(output_path).raw
        last_round = 4 + rescue_index + 1
        if simplify_scan_is_acceptable(baseline, last_scan):
            return SimplifyResult(
                success=True,
                output_path=output_path,
                rounds=last_round,
                old_triangle_count=len(mesh.triangles),
                new_triangle_count=last_triangle_count,
                old_cull_worst=old_cull_worst,
                new_cull_worst=int(last_scan["freeze"]["cullWorst"]),
                cull_verdict=int(last_scan["freeze"]["cullVerdict"]),
                verdict=last_scan["verdict"],
                verifier_passed=True,
                welding_arrays_empty=True,
                tolerance_used=_tolerance_description(baseline, last_scan),
                certification_failures=(),
            )
        progress = (
            int(last_scan["freeze"]["cullWorst"]),
            int(last_scan["freeze"]["cullVerdict"]),
            int(last_scan["orientation"]["inverted"]),
            int(last_scan["winding_cull"]["inverted"]),
            int(last_scan["invisible_walls"]["count"]),
            last_triangle_count,
        )
        if progress == previous_progress:
            stalled_candidates += 1
            if stalled_candidates >= 1:
                break
        else:
            stalled_candidates = 0
        previous_progress = progress
        new_overrides = _defect_component_overrides(
            last_scan, provenance, collision, strength
        )
        if new_overrides:
            reserve = _local_reserve(
                len(provenance),
                max(
                    (len(component.source_faces) for component in provenance),
                    default=0,
                ),
            )
            if reserve >= 512:
                component_overrides.update(new_overrides)
            else:
                component_overrides = new_overrides
        has_safety_defects = _has_safety_defects(last_scan, baseline)
        retain_topology_overrides = (
            topology_reserve == 8
            and component_count > 10
            and bool(component_overrides)
        )
        if not has_safety_defects and not retain_topology_overrides:
            component_overrides.clear()
        if component_count > 3 and has_safety_defects:
            local_only = True
        else:
            local_only = False
            reduction_index += 1

    output_path.unlink(missing_ok=True)
    return SimplifyResult(
        success=False,
        output_path=output_path,
        rounds=last_round,
        old_triangle_count=len(mesh.triangles),
        new_triangle_count=last_triangle_count,
        old_cull_worst=old_cull_worst,
        new_cull_worst=(int(last_scan["freeze"]["cullWorst"]) if last_scan else None),
        cull_verdict=(int(last_scan["freeze"]["cullVerdict"]) if last_scan else None),
        verdict=(last_scan["verdict"] if last_scan else "not scanned"),
        verifier_passed=True,
        welding_arrays_empty=True,
        tolerance_used=_tolerance_description(baseline, last_scan),
        certification_failures=tuple(
            simplify_certification_failures(baseline, last_scan)
            if last_scan is not None
            else ("not scanned",)
        ),
        reason=(
            "simplification candidates were rejected: "
            + ", ".join(
                simplify_certification_failures(baseline, last_scan)
                if last_scan is not None else ("not scanned",)
            )
        ),
    )


def _multi_targets(triangle_count: int) -> tuple[int, ...]:
    """Return a short, deterministic target ladder for one MOPP group."""
    first = min(1500, max(200, triangle_count))
    values = [first]
    while len(values) < 8:
        next_target = max(200, math.floor(values[-1] * 0.75))
        if next_target >= values[-1]:
            break
        values.append(next_target)
    return tuple(values)


def _encode_collision_replacement(
    layout: NifFileLayout,
    collision: CollisionInfo,
    mesh: CollisionMesh,
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    materials: list[int],
) -> dict[int, bytes]:
    encoded = _encode_compressed_mesh(mesh, vertices, triangles, materials)
    code, origin, scale, _, _ = _compile_verified_mopp(
        encoded.vertices,
        encoded.triangles,
        encoded.output_ids,
        mesh.radius,
    )
    return {
        mesh.data_block_index: encoded.payload,
        collision.shape_block_index: _serialize_mopp(
            read_mopp(layout, collision.shape_block_index), code, origin, scale
        ),
    }


def _simplify_multi_collision(
    input_path: Path,
    output_path: Path,
    strength: str,
    deadmesh_dir: str | Path | None,
    stop_check: Callable[[], None],
    item_progress: Callable[[str], None],
) -> SimplifyResult:
    """Simplify independent MOPP groups with whole-file certification."""
    from dmfix.core.fixes.degenerate import _scanner

    layout = NifFileLayout.read(input_path)
    collisions = locate_collisions(input_path)
    if not collisions:
        raise ValueError("no MOPP collision found")
    if any(c.shape_chain[1] != "bhkCompressedMeshShape" for c in collisions):
        raise ValueError("UNSUPPORTED_MULTI_MOPP: child shape is not compressed mesh")
    scanner = _scanner(deadmesh_dir)
    item_progress("baseline DeadMesh scan")
    stop_check()
    baseline = scanner.scan_file(input_path).raw
    if int((baseline.get("freeze") or {}).get("strayRisk", 0)) > 0:
        raise ValueError(
            "UNSUPPORTED_STRAY_CHUNK: multi-MOPP collision has an outlying chunk"
        )

    meshes = [decode_compressed_mesh(layout, c.child_shape_block_index) for c in collisions]
    data_blocks = [mesh.data_block_index for mesh in meshes]
    if len(set(data_blocks)) != len(data_blocks):
        raise ValueError("UNSUPPORTED_MULTI_MOPP: collision groups share mesh data")
    order = sorted(range(len(collisions)), key=lambda i: len(meshes[i].triangles), reverse=True)
    seen_candidates: set[str] = set()
    rounds = 0
    last_scan = baseline
    last_triangle_count = sum(len(mesh.triangles) for mesh in meshes)
    old_triangle_count = last_triangle_count
    old_cull_worst = int(baseline["freeze"]["cullWorst"])

    for group_index in order:
        collision = collisions[group_index]
        mesh = meshes[group_index]
        vertices, triangles, materials = _drop_degenerate(mesh)
        if not triangles:
            continue
        for target in _multi_targets(len(triangles)):
            if rounds >= 8:
                break
            stop_check()
            item_progress(
                f"multi-MOPP group {group_index + 1}/{len(collisions)}: target {target}"
            )
            new_vertices, new_triangles, new_materials = _simplify_by_material(
                vertices,
                triangles,
                materials,
                target,
                component_floor=1,
                aggressiveness=20.0 if strength == "aggressive" else 10.0,
                hull_threshold=0,
                repair_closed_winding=True,
                repeat_passes=2,
            )
            replacements = _encode_collision_replacement(
                layout, collision, mesh, new_vertices, new_triangles, new_materials
            )
            candidate_bytes = layout.replace_blocks(replacements)
            candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
            if candidate_hash in seen_candidates:
                output_path.unlink(missing_ok=True)
                return SimplifyResult(
                    success=False,
                    output_path=output_path,
                    rounds=rounds,
                    old_triangle_count=old_triangle_count,
                    new_triangle_count=last_triangle_count,
                    old_cull_worst=old_cull_worst,
                    new_cull_worst=int(last_scan["freeze"]["cullWorst"]),
                    cull_verdict=int(last_scan["freeze"]["cullVerdict"]),
                    verdict=last_scan["verdict"],
                    verifier_passed=True,
                    welding_arrays_empty=True,
                    tolerance_used=_tolerance_description(baseline, last_scan),
                    certification_failures=tuple(
                        simplify_certification_failures(baseline, last_scan)
                    ),
                    reason="NO_PROGRESS: repeated multi-MOPP candidate",
                )
            seen_candidates.add(candidate_hash)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(candidate_bytes)
            rounds += 1
            last_triangle_count = sum(
                len(new_triangles) if index == group_index else len(other.triangles)
                for index, other in enumerate(meshes)
            )
            stop_check()
            last_scan = scanner.scan_file(output_path).raw
            if simplify_scan_is_acceptable(baseline, last_scan):
                return SimplifyResult(
                    True,
                    output_path,
                    rounds,
                    old_triangle_count,
                    last_triangle_count,
                    old_cull_worst,
                    int(last_scan["freeze"]["cullWorst"]),
                    int(last_scan["freeze"]["cullVerdict"]),
                    last_scan["verdict"],
                    True,
                    True,
                    _tolerance_description(baseline, last_scan),
                )
    output_path.unlink(missing_ok=True)
    failures = tuple(simplify_certification_failures(baseline, last_scan))
    return SimplifyResult(
        success=False,
        output_path=output_path,
        rounds=rounds,
        old_triangle_count=old_triangle_count,
        new_triangle_count=last_triangle_count,
        old_cull_worst=old_cull_worst,
        new_cull_worst=int(last_scan["freeze"]["cullWorst"]),
        cull_verdict=int(last_scan["freeze"]["cullVerdict"]),
        verdict=last_scan["verdict"],
        verifier_passed=True,
        welding_arrays_empty=True,
        tolerance_used=_tolerance_description(baseline, last_scan),
        certification_failures=failures,
        reason="multi-MOPP candidates were rejected: "
        + (", ".join(failures) or "NO_PROGRESS"),
    )


def _drop_degenerate(
    mesh: CollisionMesh,
    *,
    geometric: bool = False,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], list[int]]:
    if len(mesh.triangle_materials) != len(mesh.triangles):
        raise ValueError("compressed mesh material count does not match triangle count")
    triangles: list[tuple[int, int, int]] = []
    materials: list[int] = []
    point_keys = {
        index: tuple(round(value, 6) for value in point)
        for index, point in enumerate(mesh.vertices)
    }
    for triangle, material in zip(mesh.triangles, mesh.triangle_materials):
        if len(set(triangle)) != 3:
            continue
        if geometric and len({point_keys[index] for index in triangle}) != 3:
            continue
        a, b, c = (np.asarray(mesh.vertices[index]) for index in triangle)
        if float(np.dot(np.cross(b - a, c - a), np.cross(b - a, c - a))) <= 1e-24:
            continue
        triangles.append(triangle)
        materials.append(material)
    return list(mesh.vertices), triangles, materials


def _simplify_by_material(
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    materials: list[int],
    target_count: int,
    component_floor: int = 8,
    aggressiveness: float = 6.0,
    hull_threshold: int = 16,
    component_overrides: dict[tuple[tuple[float, float, float], ...], tuple[int, float]] | None = None,
    repair_closed_winding: bool = False,
    return_provenance: bool = False,
    repeat_passes: int = 1,
    split_planar_patches: bool = False,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], list[int]] | tuple[
    list[tuple[float, float, float]],
    list[tuple[int, int, int]],
    list[int],
    tuple[_ComponentProvenance, ...],
]:
    groups: dict[int, list[int]] = defaultdict(list)
    for face_index, material in enumerate(materials):
        groups[material].append(face_index)

    result_vertices: list[tuple[float, float, float]] = []
    result_triangles: list[tuple[int, int, int]] = []
    result_materials: list[int] = []
    provenance: list[_ComponentProvenance] = []
    for material in sorted(groups):
        face_indexes = groups[material]
        material_vertex_start = len(result_vertices)
        material_triangle_start = len(result_triangles)
        group_target = round(target_count * len(face_indexes) / len(triangles))
        group_target = min(len(face_indexes), max(8, group_target))
        material_hull_used = False
        components = _connected_face_components(face_indexes, vertices, triangles)
        for component in components:
            component_target = round(group_target * len(component) / len(face_indexes))
            component_target = min(len(component), max(component_floor, component_target))
            used_keys = sorted(
                {
                    _vertex_key(vertices[index])
                    for fi in component
                    for index in triangles[fi]
                }
            )
            component_key = tuple(used_keys)
            remap = {key: new for new, key in enumerate(used_keys)}
            points = np.asarray(used_keys, dtype=np.float64)
            original_faces = np.asarray(
                [
                    tuple(remap[_vertex_key(vertices[index])] for index in triangles[fi])
                    for fi in component
                ],
                dtype=np.int32,
            )
            faces = original_faces
            override = (component_overrides or {}).get(component_key)
            component_aggressiveness = aggressiveness
            if override is not None:
                component_target = min(len(component), max(component_floor, override[0]))
                component_aggressiveness = override[1]
            is_small_island = len(component) < hull_threshold
            hull = _convex_hull(points) if is_small_island else None
            # A hull over an open/concave island can hold MORE triangles than
            # the island itself and adds collision where nothing is drawn
            # (invisible walls, which dmscan flags). Only take the hull when it
            # actually reduces the triangle count.
            if hull is not None and len(hull) >= len(component):
                hull = None
            was_simplified = False
            if hull is not None:
                material_hull_used = True
                faces = np.asarray(hull, dtype=np.int32)
            elif not is_small_island and component_target < len(faces):
                points, faces = fast_simplification.simplify(
                    points,
                    faces,
                    target_count=component_target,
                    agg=component_aggressiveness,
                )
                for _ in range(max(0, repeat_passes - 1)):
                    if len(faces) <= component_target:
                        break
                    points, faces = fast_simplification.simplify(
                        points,
                        faces,
                        target_count=component_target,
                        agg=component_aggressiveness,
                    )
                was_simplified = True

                if split_planar_patches:
                    patch_result = _simplify_thin_component_patches(
                        np.asarray(used_keys, dtype=np.float64),
                        original_faces,
                        component_target,
                        component_aggressiveness,
                        repeat_passes,
                    )
                    if patch_result is not None and len(patch_result[1]) < len(faces):
                        points, faces = patch_result

            if was_simplified:
                faces = _repair_component_orientation(
                    points, faces, np.asarray(used_keys, dtype=np.float64), original_faces
                )
                if repair_closed_winding:
                    faces = _repair_closed_component_winding(
                        points,
                        faces,
                        np.asarray(used_keys, dtype=np.float64),
                        original_faces,
                    )
                # Collapsing sliver triangles can fling a vertex far off the
                # original surface; the spike then reads as collision where
                # nothing is drawn (invisible wall, ~0.5 Havok unit = 35
                # Skyrim units at dmscan's default gap). Revert the whole
                # component if any decimated vertex drifted implausibly far
                # from the component's original vertex cloud.
                original_points = np.asarray(used_keys, dtype=np.float64)
                if len(points) and _max_nearest_distance(
                    np.asarray(points, dtype=np.float64), original_points
                ) > 0.35:
                    points = original_points
                    faces = original_faces
                    was_simplified = False

            valid_faces = []
            for face in faces:
                triangle = tuple(int(index) for index in face)
                if len(set(triangle)) != 3:
                    continue
                a, b, c = (points[index] for index in triangle)
                cross = np.cross(b - a, c - a)
                if float(np.dot(cross, cross)) > 1e-24:
                    valid_faces.append(triangle)
            required_component_faces = (
                4 if hull is not None else min(component_floor, len(component))
            )
            if len(valid_faces) < required_component_faces:
                points = np.asarray(used_keys, dtype=np.float64)
                valid_faces = [
                    tuple(int(index) for index in face) for face in original_faces
                ]
            vertex_base = len(result_vertices)
            result_vertices.extend(tuple(float(value) for value in point) for point in points)
            result_triangles.extend(
                tuple(vertex_base + index for index in face) for face in valid_faces
            )
            result_materials.extend([material] * len(valid_faces))
            provenance.append(
                _ComponentProvenance(
                    key=component_key,
                    points=np.asarray(points, dtype=np.float64),
                    faces=np.asarray(valid_faces, dtype=np.int32),
                    source_points=np.asarray(used_keys, dtype=np.float64),
                    source_faces=original_faces,
                )
            )

        required = min(8, len(face_indexes))
        if (
            not material_hull_used
            and len(result_triangles) - material_triangle_start < required
        ):
            del result_vertices[material_vertex_start:]
            del result_triangles[material_triangle_start:]
            del result_materials[material_triangle_start:]
            used = sorted({index for fi in face_indexes for index in triangles[fi]})
            remap = {old: new for new, old in enumerate(used)}
            result_vertices.extend(vertices[index] for index in used)
            result_triangles.extend(
                tuple(material_vertex_start + remap[index] for index in triangles[fi])
                for fi in face_indexes
            )
            result_materials.extend([material] * len(face_indexes))
    if return_provenance:
        return result_vertices, result_triangles, result_materials, tuple(provenance)
    return result_vertices, result_triangles, result_materials


def _simplify_thin_component_patches(
    points: np.ndarray,
    faces: np.ndarray,
    target_count: int,
    aggressiveness: float,
    repeat_passes: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Simplify the oriented surface patches of one large, thin component."""
    if len(faces) < 1_000 or len(points) < 4:
        return None
    extents = np.ptp(points, axis=0)
    nonzero = sorted(float(value) for value in extents if value > 1e-9)
    if len(nonzero) < 3 or nonzero[0] / nonzero[1] > 0.05:
        return None

    triangles = points[faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0],
        triangles[:, 2] - triangles[:, 0],
    )
    dominant = np.argmax(np.abs(normals), axis=1)
    groups = dominant * 2 + (
        normals[np.arange(len(normals)), dominant] < 0
    ).astype(np.int32)
    if len(set(int(group) for group in groups)) < 4:
        return None

    output_points: list[np.ndarray] = []
    output_faces: list[np.ndarray] = []
    vertex_base = 0
    for group in sorted(set(int(value) for value in groups)):
        source_faces = faces[groups == group]
        used = np.unique(source_faces)
        remap = np.full(len(points), -1, dtype=np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        local_points = points[used]
        local_faces = remap[source_faces]
        patch_target = max(4, round(target_count * len(source_faces) / len(faces)))
        for _ in range(max(1, repeat_passes)):
            if len(local_faces) <= patch_target:
                break
            local_points, local_faces = fast_simplification.simplify(
                local_points,
                local_faces,
                target_count=patch_target,
                agg=aggressiveness,
            )
        if _max_nearest_distance(local_points, points[used]) > 0.35:
            return None
        local_faces = _repair_component_orientation(
            local_points, local_faces, points[used], remap[source_faces]
        )
        output_points.append(local_points)
        output_faces.append(local_faces + vertex_base)
        vertex_base += len(local_points)

    if not output_faces:
        return None
    return np.vstack(output_points), np.vstack(output_faces)


def _repair_component_orientation(
    points: np.ndarray,
    faces: np.ndarray,
    source_points: np.ndarray,
    source_faces: np.ndarray,
) -> np.ndarray:
    if not len(faces):
        return faces
    source_triangles = source_points[source_faces]
    source_centroids = source_triangles.mean(axis=1)
    source_normals = np.cross(
        source_triangles[:, 1] - source_triangles[:, 0],
        source_triangles[:, 2] - source_triangles[:, 0],
    )
    sample_indexes = np.linspace(
        0, len(faces) - 1, min(50, len(faces)), dtype=np.int32
    )
    disagreements = 0
    compared = 0
    for face_index in sample_indexes:
        triangle = points[faces[face_index]]
        centroid = triangle.mean(axis=0)
        nearest = int(np.argmin(np.sum((source_centroids - centroid) ** 2, axis=1)))
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        dot = float(np.dot(normal, source_normals[nearest]))
        if dot != 0.0:
            compared += 1
            disagreements += dot < 0.0
    if disagreements > compared / 2:
        return faces[:, [0, 2, 1]]
    return faces


def _convex_hull(points: np.ndarray) -> list[tuple[int, int, int]] | None:
    if len(points) < 4:
        return None
    span = float(np.max(np.ptp(points, axis=0)))
    epsilon = max(1e-10, span * 1e-9)
    first = int(np.argmin(points[:, 0]))
    distances = np.sum((points - points[first]) ** 2, axis=1)
    second = int(np.argmax(distances))
    if distances[second] <= epsilon * epsilon:
        return None
    line = points[second] - points[first]
    line_distances = np.linalg.norm(np.cross(points - points[first], line), axis=1)
    third = int(np.argmax(line_distances))
    if line_distances[third] <= epsilon * np.linalg.norm(line):
        return None
    plane_normal = np.cross(points[second] - points[first], points[third] - points[first])
    plane_distances = np.abs((points - points[first]) @ plane_normal)
    fourth = int(np.argmax(plane_distances))
    if plane_distances[fourth] <= epsilon * np.linalg.norm(plane_normal):
        return None

    interior = points[[first, second, third, fourth]].mean(axis=0)

    def outward(face: tuple[int, int, int]) -> tuple[int, int, int]:
        a, b, c = (points[index] for index in face)
        if float(np.dot(np.cross(b - a, c - a), interior - a)) > 0.0:
            return (face[0], face[2], face[1])
        return face

    faces = [
        outward(face)
        for face in (
            (first, second, third),
            (first, fourth, second),
            (second, fourth, third),
            (third, fourth, first),
        )
    ]
    initial = {first, second, third, fourth}
    candidates = [index for index in range(len(points)) if index not in initial]
    while True:
        eye = None
        best_distance = epsilon
        for point_index in candidates:
            for face in faces:
                a, b, c = (points[index] for index in face)
                normal = np.cross(b - a, c - a)
                distance = float(np.dot(normal, points[point_index] - a)) / float(
                    np.linalg.norm(normal)
                )
                if distance > best_distance:
                    eye = point_index
                    best_distance = distance
        if eye is None:
            break
        visible = []
        for face in faces:
            a, b, c = (points[index] for index in face)
            normal = np.cross(b - a, c - a)
            if float(np.dot(normal, points[eye] - a)) > epsilon * float(
                np.linalg.norm(normal)
            ):
                visible.append(face)
        horizon: dict[tuple[int, int], tuple[int, int]] = {}
        for face in visible:
            for edge in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                key = tuple(sorted(edge))
                if key in horizon:
                    del horizon[key]
                else:
                    horizon[key] = edge
        faces = [face for face in faces if face not in visible]
        faces.extend(outward((edge[0], edge[1], eye)) for edge in horizon.values())
        candidates.remove(eye)

    volume = sum(
        float(np.dot(points[a], np.cross(points[b], points[c])))
        for a, b, c in faces
    ) / 6.0
    if volume < 0.0:
        faces = [(a, c, b) for a, b, c in faces]
    return faces


def _max_nearest_distance(points: np.ndarray, reference: np.ndarray) -> float:
    """Largest distance from any point to its nearest reference point (Havok units)."""
    worst = 0.0
    # Chunked to bound memory on large components.
    for start in range(0, len(points), 256):
        block = points[start : start + 256]
        deltas = block[:, None, :] - reference[None, :, :]
        nearest = np.sqrt((deltas * deltas).sum(axis=2)).min(axis=1)
        worst = max(worst, float(nearest.max()))
    return worst


def _connected_face_components(
    face_indexes: list[int],
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> list[list[int]]:
    # Chunk-local vertex tables duplicate boundary vertices. Quantized chunks
    # have 0.001-unit precision, so use the stored precision to recover the
    # actual connected components before protecting small islands.
    vertex_keys = [_vertex_key(vertex) for vertex in vertices]
    faces_by_vertex: dict[tuple[float, float, float], list[int]] = defaultdict(list)
    for face_index in face_indexes:
        for vertex_index in triangles[face_index]:
            faces_by_vertex[vertex_keys[vertex_index]].append(face_index)

    remaining = set(face_indexes)
    components: list[list[int]] = []
    while remaining:
        pending = [min(remaining)]
        remaining.remove(pending[0])
        component: list[int] = []
        while pending:
            face_index = pending.pop()
            component.append(face_index)
            for vertex_index in triangles[face_index]:
                for neighbor in faces_by_vertex[vertex_keys[vertex_index]]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
        components.append(sorted(component))
    return components


def _vertex_key(vertex: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(round(value, 3) for value in vertex)


def _encode_compressed_mesh(
    source: CollisionMesh,
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    materials: list[int],
) -> _EncodedMesh:
    if source.auxiliary_counts != (0, 0, 0):
        raise ValueError("non-empty compressed-mesh auxiliary arrays are not supported")
    if not source.transforms:
        raise ValueError("compressed mesh has no transform")
    if len(triangles) != len(materials):
        raise ValueError("triangle material count mismatch")

    from pyn.mesh_segment import segment_mesh
    from pyn.tri_strip import stripify

    source_entries = {material: extra for material, extra in source.material_entries}
    # Big triangles may use Havok's 0xFFFF default-material sentinel, which
    # decodes to material 0 even when the source table has no explicit entry.
    if 0 in set(materials) and 0 not in source_entries:
        source_entries[0] = source.material_entries[0][1] if source.material_entries else 0
    unique_materials = sorted(set(materials))
    missing = set(unique_materials) - set(source_entries)
    if missing:
        raise ValueError(f"materials missing from source table: {sorted(missing)}")
    material_entries = [(material, source_entries[material]) for material in unique_materials]
    material_indexes = {material: index for index, material in enumerate(unique_materials)}

    chunk_groups: list[tuple[int, list[int]]] = []
    big_face_indexes: list[int] = []
    for material in unique_materials:
        material_faces = [index for index, value in enumerate(materials) if value == material]
        material_tris = [triangles[index] for index in material_faces]
        for local_group in segment_mesh(vertices, material_tris):
            group = [material_faces[index] for index in local_group]
            used = {vertex for face_index in group for vertex in triangles[face_index]}
            extent = max(
                max(vertices[index][axis] for index in used)
                - min(vertices[index][axis] for index in used)
                for axis in range(3)
            )
            if extent > MAX_QUANTIZED_EXTENT:
                big_face_indexes.extend(group)
            else:
                chunk_groups.append((material, group))

    big_used = sorted(
        {vertex for face_index in big_face_indexes for vertex in triangles[face_index]}
    )
    if len(big_used) > 65535:
        raise ValueError("big-vertex table exceeds uint16 index capacity")
    big_remap = {old: new for new, old in enumerate(big_used)}

    if not triangles:
        raise ValueError("cannot encode an empty compressed mesh")
    used_vertices = [vertices[index] for triangle in triangles for index in triangle]
    minimum = tuple(min(vertex[axis] for vertex in used_vertices) for axis in range(3))
    maximum = tuple(max(vertex[axis] for vertex in used_vertices) for axis in range(3))

    payload = bytearray()
    payload.extend(
        struct.pack(
            "<4I f 8f 2B",
            source.bits_per_index,
            source.bits_per_w_index,
            source.mask_w_index,
            source.mask_index,
            source.error,
            *minimum,
            0.0,
            *maximum,
            1.0,
            source.welding_type,
            source.material_type,
        )
    )
    payload.extend(struct.pack("<3I", 0, 0, 0))
    payload.extend(struct.pack("<I", len(material_entries)))
    for entry in material_entries:
        payload.extend(struct.pack("<2I", *entry))
    payload.extend(struct.pack("<I", 0))  # named materials
    payload.extend(struct.pack("<I", len(source.transforms)))
    for transform in source.transforms:
        payload.extend(struct.pack("<8f", *transform))

    encoded_vertices: list[tuple[float, float, float]] = [vertices[index] for index in big_used]
    encoded_triangles: list[tuple[int, int, int]] = []
    output_ids: list[int] = []
    payload.extend(struct.pack("<I", len(big_used)))
    for index in big_used:
        payload.extend(struct.pack("<4f", *vertices[index], 0.0))
    payload.extend(struct.pack("<I", len(big_face_indexes)))
    for output_id, face_index in enumerate(big_face_indexes):
        triangle = tuple(big_remap[index] for index in triangles[face_index])
        material = materials[face_index]
        raw_material = 0xFFFF if material == 0 else material_indexes[material]
        payload.extend(struct.pack("<3H I H", *triangle, raw_material, 0))
        encoded_triangles.append(triangle)
        output_ids.append(output_id)

    payload.extend(struct.pack("<I", len(chunk_groups)))
    for chunk_index, (material, face_group) in enumerate(chunk_groups):
        used = sorted({index for face_index in face_group for index in triangles[face_index]})
        remap = {old: new for new, old in enumerate(used)}
        local_triangles = [
            tuple(remap[index] for index in triangles[face_index])
            for face_index in face_group
        ]
        local_vertices = [vertices[index] for index in used]
        translation = tuple(min(vertex[axis] for vertex in local_vertices) for axis in range(3))
        quantized = [
            tuple(
                max(0, min(65535, round((vertex[axis] - translation[axis]) * 1000.0)))
                for axis in range(3)
            )
            for vertex in local_vertices
        ]
        strips, leftovers = stripify(local_triangles)
        indexes = [index for strip in strips for index in strip]
        strip_lengths = [len(strip) for strip in strips]
        for triangle in leftovers:
            indexes.extend(triangle)

        payload.extend(struct.pack("<4f", *translation, 0.0))
        payload.extend(struct.pack("<IHH", material_indexes[material], 0xFFFF, 0))
        components = [component for vertex in quantized for component in vertex]
        payload.extend(struct.pack("<I", len(components)))
        if components:
            payload.extend(struct.pack(f"<{len(components)}H", *components))
        payload.extend(struct.pack("<I", len(indexes)))
        if indexes:
            payload.extend(struct.pack(f"<{len(indexes)}H", *indexes))
        payload.extend(struct.pack("<I", len(strip_lengths)))
        if strip_lengths:
            payload.extend(struct.pack(f"<{len(strip_lengths)}H", *strip_lengths))
        payload.extend(struct.pack("<I", 0))  # welding array

        vertex_base = len(encoded_vertices)
        encoded_vertices.extend(
            tuple(quantized[index][axis] / 1000.0 + translation[axis] for axis in range(3))
            for index in range(len(quantized))
        )
        index_position = 0
        chunk_prefix = (chunk_index + 1) << source.bits_per_w_index
        for strip in strips:
            for winding in range(len(strip) - 2):
                if winding & 1:
                    local = (strip[winding], strip[winding + 2], strip[winding + 1])
                else:
                    local = (strip[winding], strip[winding + 1], strip[winding + 2])
                encoded_triangles.append(tuple(vertex_base + index for index in local))
                output_ids.append(
                    chunk_prefix
                    | ((winding & 1) << source.bits_per_index)
                    | (index_position + winding)
                )
            index_position += len(strip)
        for offset in range(0, len(leftovers) * 3, 3):
            local = tuple(indexes[index_position + offset + index] for index in range(3))
            encoded_triangles.append(tuple(vertex_base + index for index in local))
            output_ids.append(chunk_prefix | (index_position + offset))

    payload.extend(struct.pack("<I", 0))  # convex pieces
    return _EncodedMesh(
        payload=bytes(payload),
        vertices=tuple(encoded_vertices),
        triangles=tuple(encoded_triangles),
        output_ids=tuple(output_ids),
    )


def _tolerance_description(baseline: dict, scan: dict | None) -> str:
    if scan is None:
        return "not scanned"
    failures = []
    verdict = scan["verdict"].upper()
    if scan["status"] == "BROKEN":
        failures.append("status=BROKEN")
    if any(word in verdict for word in ("HEAVY", "CRASH", "HANG")):
        failures.append(f"verdict={scan['verdict']}")
    if scan["broken"]["refs"] != 0:
        failures.append(f"broken.refs={scan['broken']['refs']}")
    if scan["freeze"]["cullVerdict"] >= 1:
        failures.append(f"cullVerdict={scan['freeze']['cullVerdict']}")
    for section, field in (
        ("orientation", "inverted"),
        ("winding_cull", "inverted"),
        ("degenerate", "tris"),
    ):
        old = baseline[section][field]
        new = scan[section][field]
        if field == "tris":
            old = old["count"]
            new = new["count"]
        if new > old:
            failures.append(f"{section}.{field}={new}>{old}")
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        levels = {"none": 0, "low": 1, "high": 2}
        old_level = baseline["fall_through_risk"]["level"]
        new_level = scan["fall_through_risk"]["level"]
        if levels.get(new_level, math.inf) > levels.get(old_level, -math.inf):
            failures.append(f"fall_through_risk.level={new_level}>{old_level}")
        for section, field in (
            ("fall_patch", "sites"),
            (None, "holes_enclosed"),
            ("invisible_walls", "count"),
        ):
            old = baseline[field] if section is None else baseline[section][field]
            new = scan[field] if section is None else scan[section][field]
            if new > old:
                name = field if section is None else f"{section}.{field}"
                failures.append(f"{name}={new}>{old}")
    return ", ".join(failures) or "accepted"

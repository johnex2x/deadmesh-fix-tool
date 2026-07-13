from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from dmfix.core.fixes._mesh_rewrite import write_collision_mesh
from dmfix.core.fixes.acceptance import nothing_got_worse
from dmfix.core.fixes.degenerate import _scanner
from dmfix.core.fixes.mopp_rebuild import decode_compressed_mesh
from dmfix.core.fixes.simplify import _connected_face_components
from dmfix.core.nif_io import NifFileLayout, locate_collisions
from dmfix.core.scanner import DmScanError


@dataclass(frozen=True)
class WindingResult:
    success: bool
    output_path: Path
    reason: str
    flipped_triangle_count: int
    baseline_scan: dict | None
    scan: dict | None


def fix_inverted(input_path: str | Path, output_path: str | Path) -> WindingResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    baseline: dict | None = None
    try:
        scanner = _scanner()
        baseline = scanner.scan_file(input_path).raw
        layout = NifFileLayout.read(input_path)
        collisions = locate_collisions(input_path)
        if len(collisions) != 1:
            raise ValueError(f"expected exactly one MOPP collision, found {len(collisions)}")
        collision = collisions[0]
        if collision.shape_chain[1] != "bhkCompressedMeshShape":
            raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")
        mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
        to_flip = _faces_to_flip(mesh.vertices, mesh.triangles, baseline)
        if not to_flip:
            raise ValueError("dmscan did not identify an unambiguous inverted component")
        triangles = [
            (a, c, b) if index in to_flip else (a, b, c)
            for index, (a, b, c) in enumerate(mesh.triangles)
        ]
        write_collision_mesh(
            layout,
            collision,
            mesh,
            list(mesh.vertices),
            triangles,
            list(mesh.triangle_materials),
            output_path,
        )
        scan = scanner.scan_file(output_path).raw
        accepted = (
            scan["orientation"]["inverted"] == 0
            and scan["winding_cull"]["inverted"]
            < baseline["winding_cull"]["inverted"]
            and nothing_got_worse(
                baseline,
                scan,
                ignore=frozenset({"orientation_inverted", "winding_inverted"}),
            )
        )
        if not accepted:
            output_path.unlink(missing_ok=True)
            return WindingResult(
                False,
                output_path,
                "dmscan verification failed or another defect got worse",
                len(to_flip),
                baseline,
                scan,
            )
        return WindingResult(
            True,
            output_path,
            f"flipped {len(to_flip)} triangles",
            len(to_flip),
            baseline,
            scan,
        )
    except (ValueError, OSError, DmScanError) as exc:
        output_path.unlink(missing_ok=True)
        return WindingResult(False, output_path, str(exc), 0, baseline, None)


def _faces_to_flip(
    vertices: tuple[tuple[float, float, float], ...],
    triangles: tuple[tuple[int, int, int], ...],
    scan: dict,
) -> set[int]:
    winding = scan["winding_cull"]
    if winding["tris"] and winding["inverted"] >= math.ceil(winding["tris"] * 0.9):
        return set(range(len(triangles)))

    descriptors = list(scan["orientation"].get("bad_components", []))
    if not descriptors:
        return set()
    components = _connected_face_components(
        list(range(len(triangles))), list(vertices), list(triangles)
    )
    matched: set[int] = set()
    used_components: set[int] = set()
    for descriptor in descriptors:
        point = descriptor.get("at") if isinstance(descriptor, dict) else None
        if not isinstance(point, list) or len(point) != 3:
            return set()
        candidates: list[tuple[float, int]] = []
        for component_index, component in enumerate(components):
            if component_index in used_components:
                continue
            points = [vertices[vertex] for face in component for vertex in triangles[face]]
            minimum = [min(value[axis] for value in points) for axis in range(3)]
            maximum = [max(value[axis] for value in points) for axis in range(3)]
            tolerance = 0.01
            if not all(
                minimum[axis] - tolerance <= point[axis] <= maximum[axis] + tolerance
                for axis in range(3)
            ):
                continue
            centroid = [
                sum(value[axis] for value in points) / len(points) for axis in range(3)
            ]
            distance = sum((centroid[axis] - point[axis]) ** 2 for axis in range(3))
            candidates.append((distance, component_index))
        candidates.sort()
        if not candidates:
            return set()
        if len(candidates) > 1 and math.isclose(
            candidates[0][0], candidates[1][0], rel_tol=0.05, abs_tol=1e-6
        ):
            return set()
        component_index = candidates[0][1]
        used_components.add(component_index)
        matched.update(components[component_index])
    return matched

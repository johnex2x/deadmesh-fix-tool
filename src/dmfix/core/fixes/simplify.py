from __future__ import annotations

import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import fast_simplification
import numpy as np

from dmfix.core.fixes.mopp_rebuild import (
    CollisionMesh,
    _compile_verified_mopp,
    _serialize_mopp,
    decode_compressed_mesh,
)
from dmfix.core.fixes.acceptance import simplify_scan_is_acceptable
from dmfix.core.nif_io import NifFileLayout, locate_collisions, read_mopp
from dmfix.core.scanner import DmScan, find_deadmesh_dir


MAX_QUANTIZED_EXTENT = 65.535
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


@dataclass(frozen=True)
class _EncodedMesh:
    payload: bytes
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    output_ids: tuple[int, ...]


def simplify_collision(
    input_path: str | Path,
    output_path: str | Path,
    strength: str = "normal",
) -> SimplifyResult:
    if strength not in STRENGTH_RATIOS:
        raise ValueError(f"unknown simplification strength {strength!r}")

    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    layout = NifFileLayout.read(input_path)
    collisions = locate_collisions(input_path)
    if len(collisions) != 1:
        raise ValueError(f"expected exactly one MOPP collision, found {len(collisions)}")
    collision = collisions[0]
    if collision.shape_chain[1] != "bhkCompressedMeshShape":
        raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")

    shape_payload = layout.payload(collision.child_shape_block_index)
    old_mopp = read_mopp(layout, collision.shape_block_index)
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    vertices, triangles, materials = _drop_degenerate(mesh)
    if not triangles:
        raise ValueError("collision mesh has no non-degenerate triangles")

    checkout_scanner = (
        Path(__file__).resolve().parents[4].parent / "DeadMesh - MOPP Collision Validator"
    )
    scanner_dir = find_deadmesh_dir([checkout_scanner])
    if scanner_dir is None:
        raise ValueError("dmscan.exe could not be located")
    scanner = DmScan(scanner_dir)
    baseline = scanner.scan_file(input_path).raw
    old_cull_worst = int(baseline["freeze"]["cullWorst"])
    initial_target = min(
        max(200, math.floor(len(triangles) * STRENGTH_RATIOS[strength])),
        1500,
    )

    last_scan: dict | None = None
    last_triangle_count = 0
    for round_number in range(1, 4):
        target = max(1, initial_target // (2 ** (round_number - 1)))
        new_vertices, new_triangles, new_materials = _simplify_by_material(
            vertices,
            triangles,
            materials,
            target,
            component_floor=(8, 2, 1)[round_number - 1],
            aggressiveness=(6.0, 8.0, 10.0)[round_number - 1],
        )
        encoded = _encode_compressed_mesh(mesh, new_vertices, new_triangles, new_materials)
        last_triangle_count = len(encoded.triangles)

        code, origin, scale, _, _ = _compile_verified_mopp(
            encoded.vertices,
            encoded.triangles,
            encoded.output_ids,
            mesh.radius,
        )
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

        last_scan = scanner.scan_file(output_path).raw
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
            )

    output_path.unlink(missing_ok=True)
    return SimplifyResult(
        success=False,
        output_path=output_path,
        rounds=3,
        old_triangle_count=len(mesh.triangles),
        new_triangle_count=last_triangle_count,
        old_cull_worst=old_cull_worst,
        new_cull_worst=(int(last_scan["freeze"]["cullWorst"]) if last_scan else None),
        cull_verdict=(int(last_scan["freeze"]["cullVerdict"]) if last_scan else None),
        verdict=(last_scan["verdict"] if last_scan else "not scanned"),
        verifier_passed=True,
        welding_arrays_empty=True,
        tolerance_used=_tolerance_description(baseline, last_scan),
    )


def _drop_degenerate(
    mesh: CollisionMesh,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], list[int]]:
    if len(mesh.triangle_materials) != len(mesh.triangles):
        raise ValueError("compressed mesh material count does not match triangle count")
    triangles: list[tuple[int, int, int]] = []
    materials: list[int] = []
    for triangle, material in zip(mesh.triangles, mesh.triangle_materials):
        if len(set(triangle)) != 3:
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
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], list[int]]:
    groups: dict[int, list[int]] = defaultdict(list)
    for face_index, material in enumerate(materials):
        groups[material].append(face_index)

    result_vertices: list[tuple[float, float, float]] = []
    result_triangles: list[tuple[int, int, int]] = []
    result_materials: list[int] = []
    for material in sorted(groups):
        face_indexes = groups[material]
        material_vertex_start = len(result_vertices)
        material_triangle_start = len(result_triangles)
        group_target = round(target_count * len(face_indexes) / len(triangles))
        group_target = min(len(face_indexes), max(8, group_target))
        for component in _connected_face_components(face_indexes, vertices, triangles):
            component_target = round(group_target * len(component) / len(face_indexes))
            component_target = min(len(component), max(component_floor, component_target))
            used_keys = sorted(
                {
                    _vertex_key(vertices[index])
                    for fi in component
                    for index in triangles[fi]
                }
            )
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
            if component_target < len(faces):
                points, faces = fast_simplification.simplify(
                    points,
                    faces,
                    target_count=component_target,
                    agg=aggressiveness,
                )

            valid_faces = []
            for face in faces:
                triangle = tuple(int(index) for index in face)
                if len(set(triangle)) != 3:
                    continue
                a, b, c = (points[index] for index in triangle)
                cross = np.cross(b - a, c - a)
                if float(np.dot(cross, cross)) > 1e-24:
                    valid_faces.append(triangle)
            if len(valid_faces) < min(component_floor, len(component)):
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

        required = min(8, len(face_indexes))
        if len(result_triangles) - material_triangle_start < required:
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
    return result_vertices, result_triangles, result_materials


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
        payload.extend(
            struct.pack(
                "<3H I H",
                *triangle,
                material_indexes[materials[face_index]],
                0,
            )
        )
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
    if scan and baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        limit = baseline["holes"]["count"] * 1.25 + 10
        return f"holes {scan['holes']['count']} <= {limit:g} (baseline +25% +10)"
    return "ray tolerance not evaluated"

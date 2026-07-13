from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from dmfix.core.nif_io import (
    MOPP_FIXED_SIZE,
    MoppData,
    NifFileLayout,
    locate_collisions,
    read_mopp,
)


MOPP_SCALE_NUMERATOR = 254.0 * 256.0 * 256.0


@dataclass(frozen=True)
class CollisionMesh:
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    output_ids: tuple[int, ...]
    radius: float
    bits_per_index: int
    bits_per_w_index: int
    data_block_index: int
    triangle_materials: tuple[int, ...] = ()
    mask_w_index: int = 0
    mask_index: int = 0
    error: float = 0.001
    welding_type: int = 0
    material_type: int = 0
    auxiliary_counts: tuple[int, int, int] = (0, 0, 0)
    material_entries: tuple[tuple[int, int], ...] = ()
    transforms: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True)
class MoppRebuildResult:
    output_path: Path
    mopp_block_index: int
    old_block_size: int
    new_block_size: int
    triangle_count: int
    verifier_passed: bool
    verifier_messages: tuple[str, ...]
    surface_reachability: float
    old_false_positive_rate: float
    new_false_positive_rate: float


def rebuild_mopp(input_path: str | Path, output_path: str | Path) -> MoppRebuildResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    layout = NifFileLayout.read(input_path)
    collisions = locate_collisions(input_path)
    if len(collisions) != 1:
        raise ValueError(f"expected exactly one MOPP collision, found {len(collisions)}")
    collision = collisions[0]
    if collision.shape_chain[1] != "bhkCompressedMeshShape":
        raise ValueError(f"unsupported MOPP child shape {collision.shape_chain[1]}")

    old_mopp = read_mopp(layout, collision.shape_block_index)
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    new_code, origin, scale, messages, verifier = _compile_verified_mopp(
        mesh.vertices,
        mesh.triangles,
        mesh.output_ids,
        mesh.radius,
    )
    largest_dim = MOPP_SCALE_NUMERATOR / scale

    old_largest_dim = MOPP_SCALE_NUMERATOR / old_mopp.scale
    old_false_positive_rate, _ = verifier.verify_tightness(
        old_mopp.code,
        old_mopp.origin,
        old_largest_dim,
        mesh.vertices,
        mesh.triangles,
        mesh.radius,
    )
    new_false_positive_rate, _ = verifier.verify_tightness(
        new_code,
        origin,
        largest_dim,
        mesh.vertices,
        mesh.triangles,
        mesh.radius,
    )

    new_payload = _serialize_mopp(old_mopp, new_code, origin, scale)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(layout.replace_block(collision.shape_block_index, new_payload))
    return MoppRebuildResult(
        output_path=output_path,
        mopp_block_index=collision.shape_block_index,
        old_block_size=layout.blocks[collision.shape_block_index].size,
        new_block_size=len(new_payload),
        triangle_count=len(mesh.triangles),
        verifier_passed=True,
        verifier_messages=tuple(messages),
        surface_reachability=1.0,
        old_false_positive_rate=old_false_positive_rate,
        new_false_positive_rate=new_false_positive_rate,
    )


def _compile_verified_mopp(
    vertices: tuple[tuple[float, float, float], ...],
    triangles: tuple[tuple[int, int, int], ...],
    output_ids: tuple[int, ...],
    radius: float,
):
    compile_mopp, verifier = _load_mopp_tools()
    code, origin, scale = compile_mopp(
        vertices,
        triangles,
        radius=radius,
        output_ids=list(output_ids),
    )
    largest_dim = MOPP_SCALE_NUMERATOR / scale
    passed, messages = verifier.verify_all(
        code,
        origin,
        largest_dim,
        vertices,
        triangles,
        output_ids,
        radius,
    )
    surface_ok, surface_messages = verifier.verify_surface_reachability(
        code,
        origin,
        largest_dim,
        vertices,
        triangles,
        radius,
    )
    messages.extend(["\n=== Surface reachability ===", *surface_messages])
    if not passed or not surface_ok:
        raise ValueError("MOPP failed internal verification:\n" + "\n".join(messages))
    return code, origin, scale, messages, verifier


def _serialize_mopp(
    old_mopp: MoppData,
    code: bytes,
    origin: tuple[float, float, float],
    scale: float,
) -> bytes:
    payload = struct.pack(
        "<iIII f I 3f f B",
        old_mopp.child_shape_index,
        *old_mopp.unused,
        old_mopp.shape_scale,
        len(code),
        *origin,
        scale,
        1,
    ) + code
    if len(payload) != MOPP_FIXED_SIZE + len(code):
        raise AssertionError("incorrect MOPP payload size")
    return payload


def decode_compressed_mesh(layout: NifFileLayout, shape_block_index: int) -> CollisionMesh:
    shape_block = layout.blocks[shape_block_index]
    shape = layout.payload(shape_block_index)
    if shape_block.type_name != "bhkCompressedMeshShape" or len(shape) != 56:
        raise ValueError("unsupported bhkCompressedMeshShape layout")
    radius = struct.unpack_from("<f", shape, 8)[0]
    data_block_index = struct.unpack_from("<i", shape, 52)[0]
    data_block = layout.blocks[data_block_index]
    if data_block.type_name != "bhkCompressedMeshShapeData":
        raise ValueError("compressed mesh shape does not reference its data block")

    data = layout.payload(data_block_index)
    pos = 0
    bits_per_index, bits_per_w_index, mask_w_index, mask_index = struct.unpack_from(
        "<4I", data, pos
    )
    pos += 16
    error = struct.unpack_from("<f", data, pos)[0]
    pos += 4 + 16 + 16  # error and AABB vectors
    welding_type, material_type = struct.unpack_from("<2B", data, pos)
    pos += 2

    auxiliary_counts: list[int] = []
    for width in (4, 2, 1):
        count = struct.unpack_from("<I", data, pos)[0]
        auxiliary_counts.append(count)
        pos += 4 + count * width

    material_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    material_entries = [
        struct.unpack_from("<2I", data, pos + index * 8)
        for index in range(material_count)
    ]
    pos += material_count * 8
    named_material_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if named_material_count:
        raise ValueError("named compressed-mesh materials are not supported")

    transform_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    transforms = [struct.unpack_from("<8f", data, pos + index * 32) for index in range(transform_count)]
    pos += transform_count * 32
    identity = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    if any(transform != identity for transform in transforms):
        raise ValueError("non-identity compressed-mesh transforms are not supported")

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    output_ids: list[int] = []
    triangle_materials: list[int] = []

    big_vertex_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    for _ in range(big_vertex_count):
        x, y, z, _ = struct.unpack_from("<4f", data, pos)
        vertices.append((x, y, z))
        pos += 16

    big_triangle_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    for triangle_index in range(big_triangle_count):
        a, b, c = struct.unpack_from("<3H", data, pos)
        material_index = struct.unpack_from("<I", data, pos + 6)[0]
        if material_index >= len(material_entries):
            raise ValueError(
                f"big triangle {triangle_index} has invalid material index {material_index}"
            )
        triangles.append((a, b, c))
        output_ids.append(triangle_index)
        triangle_materials.append(material_entries[material_index][0])
        pos += 12  # indices, material, welding

    chunk_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    for chunk_index in range(chunk_count):
        translation = struct.unpack_from("<4f", data, pos)
        pos += 16
        material_index = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if material_index >= len(material_entries):
            raise ValueError(f"chunk {chunk_index} has invalid material index {material_index}")
        chunk_material = material_entries[material_index][0]
        _, transform_index = struct.unpack_from("<HH", data, pos)
        pos += 4
        if transform_index >= len(transforms):
            raise ValueError(f"chunk {chunk_index} has invalid transform index {transform_index}")

        component_count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if component_count % 3:
            raise ValueError(f"chunk {chunk_index} has incomplete quantized vertex data")
        components = struct.unpack_from(f"<{component_count}H", data, pos)
        pos += component_count * 2
        vertex_base = len(vertices)
        for index in range(0, component_count, 3):
            vertices.append(
                (
                    components[index] / 1000.0 + translation[0],
                    components[index + 1] / 1000.0 + translation[1],
                    components[index + 2] / 1000.0 + translation[2],
                )
            )

        index_count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        indexes = struct.unpack_from(f"<{index_count}H", data, pos)
        pos += index_count * 2
        strip_count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        strip_lengths = struct.unpack_from(f"<{strip_count}H", data, pos)
        pos += strip_count * 2
        welding_count = struct.unpack_from("<I", data, pos)[0]
        pos += 4 + welding_count * 2

        index_position = 0
        chunk_prefix = (chunk_index + 1) << bits_per_w_index
        for strip_length in strip_lengths:
            for winding in range(strip_length - 2):
                if winding & 1:
                    local = (
                        indexes[index_position + winding],
                        indexes[index_position + winding + 2],
                        indexes[index_position + winding + 1],
                    )
                else:
                    local = (
                        indexes[index_position + winding],
                        indexes[index_position + winding + 1],
                        indexes[index_position + winding + 2],
                    )
                triangles.append(tuple(vertex_base + value for value in local))
                triangle_materials.append(chunk_material)
                output_ids.append(
                    chunk_prefix | ((winding & 1) << bits_per_index) | (index_position + winding)
                )
            index_position += strip_length

        remaining = index_count - index_position
        if remaining % 3:
            raise ValueError(f"chunk {chunk_index} has incomplete flat triangle data")
        for flat_position in range(index_position, index_count, 3):
            triangles.append(tuple(vertex_base + indexes[flat_position + i] for i in range(3)))
            triangle_materials.append(chunk_material)
            output_ids.append(chunk_prefix | flat_position)

    convex_piece_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if convex_piece_count or pos != len(data):
        raise ValueError("unsupported compressed-mesh convex-piece data")
    if mask_index != (1 << bits_per_index) - 1 or mask_w_index != (1 << bits_per_w_index) - 1:
        raise ValueError("compressed-mesh shape-key masks do not match bit widths")

    return CollisionMesh(
        vertices=tuple(vertices),
        triangles=tuple(triangles),
        output_ids=tuple(output_ids),
        radius=radius,
        bits_per_index=bits_per_index,
        bits_per_w_index=bits_per_w_index,
        data_block_index=data_block_index,
        triangle_materials=tuple(triangle_materials),
        mask_w_index=mask_w_index,
        mask_index=mask_index,
        error=error,
        welding_type=welding_type,
        material_type=material_type,
        auxiliary_counts=tuple(auxiliary_counts),
        material_entries=tuple(material_entries),
        transforms=tuple(transforms),
    )


def _load_mopp_tools():
    from dmfix.core.paths import ensure_vendor_on_path
    ensure_vendor_on_path()
    import mopp_verifier
    from pyn.mopp_compiler import compile_mopp

    return compile_mopp, mopp_verifier

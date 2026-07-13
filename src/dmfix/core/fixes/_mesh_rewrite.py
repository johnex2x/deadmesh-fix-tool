from __future__ import annotations

from pathlib import Path

from dmfix.core.fixes.mopp_rebuild import (
    CollisionMesh,
    _compile_verified_mopp,
    _serialize_mopp,
)
from dmfix.core.fixes.simplify import _encode_compressed_mesh
from dmfix.core.nif_io import CollisionInfo, NifFileLayout, read_mopp


def write_collision_mesh(
    layout: NifFileLayout,
    collision: CollisionInfo,
    mesh: CollisionMesh,
    vertices: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    materials: list[int],
    output_path: Path,
) -> int:
    shape_payload = layout.payload(collision.child_shape_block_index)
    encoded = _encode_compressed_mesh(mesh, vertices, triangles, materials)
    old_mopp = read_mopp(layout, collision.shape_block_index)
    code, origin, scale, _, _ = _compile_verified_mopp(
        encoded.vertices,
        encoded.triangles,
        encoded.output_ids,
        mesh.radius,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        layout.replace_blocks(
            {
                mesh.data_block_index: encoded.payload,
                collision.shape_block_index: _serialize_mopp(
                    old_mopp, code, origin, scale
                ),
            }
        )
    )
    output_layout = NifFileLayout.read(output_path)
    if output_layout.payload(collision.child_shape_block_index) != shape_payload:
        raise AssertionError("bhkCompressedMeshShape payload changed")
    return len(encoded.triangles)

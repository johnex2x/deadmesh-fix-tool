from __future__ import annotations

import json
import struct
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dmfix.core.fixes._mesh_rewrite import write_collision_mesh
from dmfix.core.fixes.mopp_rebuild import decode_compressed_mesh
from dmfix.core.fixes.simplify import (
    _connected_face_components,
    _encode_compressed_mesh,
    simplify_collision,
)
from dmfix.core.nif_io import NifFileLayout, locate_collisions, read_mopp
from dmfix.core.scanner import DmScan


FIXTURES = ROOT / "tests" / "fixtures"
OUTPUT_DIR = ROOT / "tmp" / "other_fixes"
SCANNER_DIR = ROOT.parent / "DeadMesh - MOPP Collision Validator"


def _clean_fixture(name: str = "mush3.nif") -> Path:
    output = OUTPUT_DIR / f"clean-{name}"
    if not output.exists():
        result = simplify_collision(FIXTURES / name, output)
        assert result.success
    return output


def _rewrite_collision(
    source: Path,
    output: Path,
    triangles: list[tuple[int, int, int]],
    materials: list[int],
) -> None:
    layout = NifFileLayout.read(source)
    collision = locate_collisions(source)[0]
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    write_collision_mesh(
        layout,
        collision,
        mesh,
        list(mesh.vertices),
        triangles,
        materials,
        output,
    )


def _append_duplicate_data_block(source: Path, output: Path) -> None:
    layout = NifFileLayout.read(source)
    collision = locate_collisions(source)[0]
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    duplicate = layout.payload(mesh.data_block_index)

    data = layout.data
    pos = data.index(b"\n") + 1 + 4 + 1 + 4
    block_count_offset = pos
    block_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4 + 4
    for _ in range(3):
        length = data[pos]
        pos += 1 + length
    type_count = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    block_types: list[str] = []
    for _ in range(type_count):
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        block_types.append(data[pos : pos + length].decode("utf-8"))
        pos += length
    type_indexes_offset = pos
    size_table_offset = type_indexes_offset + block_count * 2
    size_table_end = size_table_offset + block_count * 4
    data_type_index = block_types.index("bhkCompressedMeshShapeData")

    prefix = bytearray(data[:size_table_offset])
    struct.pack_into("<I", prefix, block_count_offset, block_count + 1)
    header = b"".join(
        (
            bytes(prefix),
            struct.pack("<H", data_type_index),
            data[size_table_offset:size_table_end],
            struct.pack("<I", len(duplicate)),
            data[size_table_end : layout.header_end],
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        b"".join(
            (
                header,
                data[layout.header_end : layout.footer_offset],
                duplicate,
                data[layout.footer_offset :],
            )
        )
    )


def _defect_fields(scan: dict) -> dict:
    return {
        "status": scan["status"],
        "verdict": scan["verdict"],
        "degenerate": scan["degenerate"]["tris"]["count"],
        "orientation": scan["orientation"],
        "winding_cull": scan["winding_cull"],
        "orphan_collisions": scan["orphan_collisions"],
        "ray_status": scan["ray_status"],
        "holes": scan["holes"]["count"],
        "invisible_walls": scan["invisible_walls"]["count"],
    }


def test_degenerate_fix() -> None:
    from dmfix.core.fixes.degenerate import fix_degenerate

    clean = _clean_fixture()
    layout = NifFileLayout.read(clean)
    collision = locate_collisions(clean)[0]
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    triangles = list(mesh.triangles)
    materials = list(mesh.triangle_materials)
    for index in range(20):
        a, b, _ = mesh.triangles[index]
        triangles.append((a, a, b))
        materials.append(mesh.triangle_materials[index])

    broken = OUTPUT_DIR / "degenerate-broken.nif"
    fixed = OUTPUT_DIR / "degenerate-fixed.nif"
    _rewrite_collision(clean, broken, triangles, materials)
    scanner = DmScan(SCANNER_DIR)
    before = scanner.scan_file(broken).raw
    assert before["degenerate"]["tris"]["count"] > 0

    result = fix_degenerate(broken, fixed)
    assert result.success, result.reason
    after = scanner.scan_file(fixed).raw
    assert after["degenerate"]["tris"]["count"] == 0
    assert after["orientation"]["inverted"] <= before["orientation"]["inverted"]
    assert after["winding_cull"]["inverted"] <= before["winding_cull"]["inverted"]
    print("degenerate before=" + json.dumps(_defect_fields(before), sort_keys=True))
    print("degenerate after=" + json.dumps(_defect_fields(after), sort_keys=True))


def test_full_inversion_fix() -> None:
    from dmfix.core.fixes.winding import fix_inverted

    clean = _clean_fixture("t_crystal4.nif")
    layout = NifFileLayout.read(clean)
    collision = locate_collisions(clean)[0]
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    flipped = [(a, c, b) for a, b, c in mesh.triangles]

    broken = OUTPUT_DIR / "inverted-full-broken.nif"
    fixed = OUTPUT_DIR / "inverted-full-fixed.nif"
    _rewrite_collision(clean, broken, flipped, list(mesh.triangle_materials))
    scanner = DmScan(SCANNER_DIR)
    before = scanner.scan_file(broken).raw
    assert before["orientation"]["inverted"] > 0 or before["winding_cull"]["inverted"] > 0

    result = fix_inverted(broken, fixed)
    assert result.success, result.reason
    after = scanner.scan_file(fixed).raw
    assert after["orientation"]["inverted"] == 0
    assert after["winding_cull"]["inverted"] < before["winding_cull"]["inverted"]
    print("full inversion before=" + json.dumps(_defect_fields(before), sort_keys=True))
    print("full inversion after=" + json.dumps(_defect_fields(after), sort_keys=True))


def test_partial_inversion_fix_when_detected() -> None:
    from dmfix.core.fixes.winding import fix_inverted

    clean = _clean_fixture("alchemy_chair.nif")
    layout = NifFileLayout.read(clean)
    collision = locate_collisions(clean)[0]
    mesh = decode_compressed_mesh(layout, collision.child_shape_block_index)
    components = _connected_face_components(
        list(range(len(mesh.triangles))), list(mesh.vertices), list(mesh.triangles)
    )
    largest = set(max(components, key=len))
    triangles = [
        (a, c, b) if index in largest else (a, b, c)
        for index, (a, b, c) in enumerate(mesh.triangles)
    ]

    broken = OUTPUT_DIR / "inverted-partial-broken.nif"
    fixed = OUTPUT_DIR / "inverted-partial-fixed.nif"
    _rewrite_collision(clean, broken, triangles, list(mesh.triangle_materials))
    scanner = DmScan(SCANNER_DIR)
    before = scanner.scan_file(broken).raw
    component_detected = before["orientation"]["inverted"] > 0
    winding_detected = before["winding_cull"]["inverted"] > 0
    print("partial inversion before=" + json.dumps(_defect_fields(before), sort_keys=True))
    if not component_detected and not winding_detected:
        print("partial inversion not flagged by dmscan; full inversion remains the gate")
        return

    result = fix_inverted(broken, fixed)
    if not component_detected:
        assert not result.success
        assert not fixed.exists()
        print("partial inversion had no component descriptor; fix failed closed")
        return
    assert result.success, result.reason
    after = scanner.scan_file(fixed).raw
    assert after["orientation"]["inverted"] == 0
    assert after["winding_cull"]["inverted"] < before["winding_cull"]["inverted"]
    print("partial inversion after=" + json.dumps(_defect_fields(after), sort_keys=True))


def test_orphan_suffix_removal() -> None:
    from dmfix.core.fixes.orphan import remove_orphan_collision

    clean = _clean_fixture()
    broken = OUTPUT_DIR / "orphan-broken.nif"
    fixed = OUTPUT_DIR / "orphan-fixed.nif"
    _append_duplicate_data_block(clean, broken)
    scanner = DmScan(SCANNER_DIR)
    before = scanner.scan_file(broken).raw
    print("orphan before=" + json.dumps(_defect_fields(before), sort_keys=True))

    result = remove_orphan_collision(broken, fixed)
    assert result.success, result.reason
    assert fixed.read_bytes() == clean.read_bytes()
    after = scanner.scan_file(fixed).raw
    assert after["orphan_collisions"] == 0
    print("orphan after=" + json.dumps(_defect_fields(after), sort_keys=True))
    if before["orphan_collisions"] == 0:
        print("synthetic orphan was not flagged by dmscan; detection gate is untestable")


def test_fail_closed_paths() -> None:
    from dmfix.core.fixes.orphan import remove_orphan_collision
    from dmfix.core.fixes.winding import _faces_to_flip

    clean = _clean_fixture()
    refused = OUTPUT_DIR / "orphan-refused.nif"
    result = remove_orphan_collision(clean, refused)
    assert not result.success and result.report_only
    assert not refused.exists()

    payload_ref = OUTPUT_DIR / "orphan-payload-ref.nif"
    payload_ref_output = OUTPUT_DIR / "orphan-payload-ref-fixed.nif"
    _append_duplicate_data_block(clean, payload_ref)
    payload_layout = NifFileLayout.read(payload_ref)
    payload_data = bytearray(payload_layout.data)
    struct.pack_into(
        "<i",
        payload_data,
        payload_layout.blocks[0].end - 4,
        len(payload_layout.blocks) - 1,
    )
    payload_ref.write_bytes(payload_data)
    result = remove_orphan_collision(payload_ref, payload_ref_output)
    assert not result.success and result.report_only
    assert "possible block reference" in result.reason
    assert not payload_ref_output.exists()

    layout = NifFileLayout.read(clean)
    try:
        replace(layout, group_count=1).remove_trailing_blocks(len(layout.blocks) - 1)
    except ValueError as exc:
        assert "groups" in str(exc)
    else:
        raise AssertionError("grouped NIF suffix removal did not fail closed")

    root_ref = OUTPUT_DIR / "orphan-root-ref.nif"
    root_ref_output = OUTPUT_DIR / "orphan-root-ref-fixed.nif"
    _append_duplicate_data_block(clean, root_ref)
    root_layout = NifFileLayout.read(root_ref)
    root_data = bytearray(root_layout.data)
    root_count = struct.unpack_from("<I", root_data, root_layout.footer_offset)[0]
    assert root_count > 0
    struct.pack_into(
        "<I", root_data, root_layout.footer_offset + 4, len(root_layout.blocks) - 1
    )
    root_ref.write_bytes(root_data)
    result = remove_orphan_collision(root_ref, root_ref_output)
    assert not result.success and result.report_only
    assert "root" in result.reason
    assert not root_ref_output.exists()

    vertices = (
        (0.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (0.0, 3.0, 0.0),
        (0.5, 0.5, -1.0),
        (1.5, 0.5, 1.0),
        (1.0, 2.0, 0.0),
    )
    triangles = ((0, 1, 2), (3, 4, 5))
    ambiguous_scan = {
        "winding_cull": {"tris": 2, "inverted": 1},
        "orientation": {"bad_components": [{"at": [1.0, 1.0, 0.0]}]},
    }
    assert _faces_to_flip(vertices, triangles, ambiguous_scan) == set()
    print("fail-closed paths: referenced/root/grouped/ambiguous all refused")


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    test_degenerate_fix()
    test_full_inversion_fix()
    test_partial_inversion_fix_when_detected()
    test_orphan_suffix_removal()
    test_fail_closed_paths()
    print("5 tests passed")

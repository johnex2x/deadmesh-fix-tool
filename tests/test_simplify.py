from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dmfix.core.fixes.mopp_rebuild import decode_compressed_mesh
from dmfix.core.fixes.acceptance import simplify_scan_is_acceptable
from dmfix.core.fixes.simplify import SimplifyResult, simplify_collision
from dmfix.core.nif_io import NifFileLayout, locate_collisions
from dmfix.core.scanner import DmScan


FIXTURES = ROOT / "tests" / "fixtures"
OUTPUT_DIR = ROOT / "tmp" / "simplified"
SCANNER_DIR = ROOT.parent / "DeadMesh - MOPP Collision Validator"


def _baselines() -> list[dict]:
    return [
        json.loads(line)
        for line in (ROOT / "tests" / "fixtures_baseline.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["freeze"]["cullVerdict"] >= 1
    ]


def _assert_only_collision_data_and_mopp_changed(source: Path, output: Path) -> None:
    source_layout = NifFileLayout.read(source)
    output_layout = NifFileLayout.read(output)
    source_collision = locate_collisions(source)[0]
    output_collision = locate_collisions(output)[0]
    source_mesh = decode_compressed_mesh(
        source_layout, source_collision.child_shape_block_index
    )
    output_mesh = decode_compressed_mesh(
        output_layout, output_collision.child_shape_block_index
    )
    assert set(output_mesh.triangle_materials) == set(source_mesh.triangle_materials)
    assert len(output_mesh.triangle_materials) == len(output_mesh.triangles)
    allowed = {source_collision.shape_block_index, source_mesh.data_block_index}

    assert allowed == {
        output_collision.shape_block_index,
        output_mesh.data_block_index,
    }
    assert len(source_layout.blocks) == len(output_layout.blocks)
    assert source_layout.data[source_layout.footer_offset :] == output_layout.data[
        output_layout.footer_offset :
    ]

    source_header = bytearray(source_layout.data[: source_layout.header_end])
    output_header = bytearray(output_layout.data[: output_layout.header_end])
    for block_index in allowed:
        source_entry = source_layout.blocks[block_index].size_entry_offset
        output_entry = output_layout.blocks[block_index].size_entry_offset
        source_header[source_entry : source_entry + 4] = b"\0" * 4
        output_header[output_entry : output_entry + 4] = b"\0" * 4
    assert source_header == output_header

    for block_index in range(len(source_layout.blocks)):
        if block_index not in allowed:
            assert source_layout.payload(block_index) == output_layout.payload(block_index)


def _summary_line(name: str, result: SimplifyResult) -> str:
    status = "success" if result.success else "fail"
    new_tris = str(result.new_triangle_count) if result.new_triangle_count else "-"
    new_worst = str(result.new_cull_worst) if result.new_cull_worst is not None else "-"
    return (
        f"{name:26} {status:7} {result.rounds:>2}  "
        f"{result.old_triangle_count:>5}->{new_tris:<5}  "
        f"{result.old_cull_worst:>5}->{new_worst:<5}  "
        f"{result.verdict}"
    )


def _acceptance_failures(baseline: dict, scan: dict) -> str:
    failures = []
    verdict = scan["verdict"].upper()
    if scan["status"] == "BROKEN":
        failures.append("status")
    if any(word in verdict for word in ("HEAVY", "CRASH", "HANG")):
        failures.append("verdict")
    if scan["broken"]["refs"] != 0:
        failures.append("broken.refs")
    if scan["freeze"]["cullVerdict"] >= 1:
        failures.append("cullVerdict")
    for field in ("orientation.inverted", "winding_cull.inverted", "degenerate.tris.count"):
        parts = field.split(".")
        old_value = baseline
        new_value = scan
        for part in parts:
            old_value = old_value[part]
            new_value = new_value[part]
        if new_value > old_value:
            failures.append(field)
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        levels = {"none": 0, "low": 1, "high": 2}
        old_level = levels.get(baseline["fall_through_risk"]["level"], -1)
        new_level = levels.get(scan["fall_through_risk"]["level"], 3)
        if new_level > old_level:
            failures.append("fall_through_risk.level")
        if scan["fall_patch"]["sites"] > baseline["fall_patch"]["sites"]:
            failures.append("fall_patch.sites")
        if scan["holes_enclosed"] > baseline["holes_enclosed"]:
            failures.append("holes_enclosed")
        if scan["invisible_walls"]["count"] > baseline["invisible_walls"]["count"]:
            failures.append("invisible_walls.count")
    return ",".join(failures) or "unknown"


def test_simplify_all_heavy_fixtures() -> None:
    baselines = _baselines()
    assert len(baselines) == 17
    scanner = DmScan(SCANNER_DIR)
    failures: list[str] = []
    results: list[tuple[str, SimplifyResult]] = []

    for baseline in baselines:
        name = Path(baseline["file"]).name
        source = FIXTURES / name
        output = OUTPUT_DIR / name
        result = simplify_collision(source, output)
        results.append((name, result))
        if not result.success:
            failures.append(f"{name}: {result.tolerance_used}")
            assert not output.exists()
            continue

        scan = scanner.scan_file(output).raw
        assert scan["status"] != "BROKEN"
        verdict = scan["verdict"].upper()
        assert "HEAVY" not in verdict
        assert "CRASH" not in verdict
        assert "HANG" not in verdict
        assert scan["broken"]["refs"] == 0
        assert scan["orientation"]["inverted"] <= baseline["orientation"]["inverted"]
        assert scan["winding_cull"]["inverted"] <= baseline["winding_cull"]["inverted"]
        assert scan["degenerate"]["tris"]["count"] <= baseline["degenerate"]["tris"]["count"]

        assert simplify_scan_is_acceptable(baseline, scan), _acceptance_failures(
            baseline, scan
        )

        _assert_only_collision_data_and_mopp_changed(source, output)

    print("name                       status  rd  triangles       cullWorst       verdict")
    for name, result in results:
        print(_summary_line(name, result))
    print(f"successes={len(results) - len(failures)}/17 failures={failures}")
    assert len(results) - len(failures) >= 16


if __name__ == "__main__":
    test_simplify_all_heavy_fixtures()
    print("1 test passed")

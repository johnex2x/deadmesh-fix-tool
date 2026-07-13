from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dmfix.core.fixes.mopp_rebuild import rebuild_mopp
from dmfix.core.nif_io import NifFileLayout, locate_collisions, read_mopp


FIXTURE = ROOT / "tests" / "fixtures" / "sm_tower_roof_1.nif"
HEALTHY_FIXTURE = ROOT / "tests" / "fixtures" / "t_crystal4vanilla.nif"
SCANNER = (
    ROOT.parent
    / "DeadMesh - MOPP Collision Validator"
    / "dmscan.exe"
)


def _scan(path: Path) -> dict:
    result = subprocess.run(
        [SCANNER, "--json", path.resolve()],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def _baseline() -> dict:
    records = (
        json.loads(line)
        for line in (ROOT / "tests" / "fixtures_baseline.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return next(record for record in records if Path(record["file"]).name == FIXTURE.name)


def test_nif_access_locates_collision_and_matches_healthy_dll_mopp() -> None:
    layout = NifFileLayout.read(HEALTHY_FIXTURE)
    assert (layout.version, layout.user_version, layout.stream_version) == (
        0x14020007,
        12,
        100,
    )

    collisions = locate_collisions(HEALTHY_FIXTURE)
    assert len(collisions) == 1
    assert collisions[0].target_node_name == "Scene Root"
    assert collisions[0].shape_chain == (
        "bhkMoppBvTreeShape",
        "bhkCompressedMeshShape",
    )

    mopp = read_mopp(layout, collisions[0].shape_block_index)
    assert len(mopp.code) == mopp.data_size
    assert mopp.code == collisions[0].mopp_code
    assert mopp.origin == collisions[0].mopp_origin
    assert mopp.scale == collisions[0].mopp_scale


def test_rebuild_mopp_clears_crash_without_collateral_changes() -> None:
    output = ROOT / "tmp" / FIXTURE.name
    result = rebuild_mopp(FIXTURE, output)

    baseline = _baseline()
    scan = _scan(output)
    assert scan["status"] != "BROKEN"
    assert "CRASH" not in scan["verdict"]
    assert scan["broken"]["refs"] == 0

    assert scan["orientation"]["inverted"] <= baseline["orientation"]["inverted"]
    # The Ray-Cast hole scan only runs on structurally intact collision. On a
    # CRASH-RISK baseline dmscan reports ray_status "incomplete" and holes 0,
    # meaning "not measured", not "no holes" — pre-existing geometry gaps
    # become visible only after the MOPP is repaired (geometry bytes are
    # asserted identical below, so the fix cannot have introduced them).
    if baseline["ray_status"] == "ok":
        assert scan["holes"]["count"] <= baseline["holes"]["count"]
        assert scan["invisible_walls"]["count"] <= baseline["invisible_walls"]["count"]
    assert scan["degenerate"]["tris"]["count"] <= baseline["degenerate"]["tris"]["count"]
    assert scan["winding_cull"]["inverted"] <= baseline["winding_cull"]["inverted"]
    assert int(scan["orphan_mopp"]) <= int(baseline["orphan_mopp"])
    assert scan["orphan_collisions"] <= baseline["orphan_collisions"]

    source_layout = NifFileLayout.read(FIXTURE)
    output_layout = NifFileLayout.read(output)
    old_block = source_layout.blocks[result.mopp_block_index]
    new_block = output_layout.blocks[result.mopp_block_index]
    source = FIXTURE.read_bytes()
    fixed = output.read_bytes()

    assert source[: old_block.size_entry_offset] == fixed[: new_block.size_entry_offset]
    assert source[old_block.size_entry_offset + 4 : old_block.offset] == fixed[
        new_block.size_entry_offset + 4 : new_block.offset
    ]
    assert source[old_block.end :] == fixed[new_block.end :]
    assert result.verifier_passed
    assert result.surface_reachability == 1.0


if __name__ == "__main__":
    test_nif_access_locates_collision_and_matches_healthy_dll_mopp()
    test_rebuild_mopp_clears_crash_without_collateral_changes()
    print("2 tests passed")

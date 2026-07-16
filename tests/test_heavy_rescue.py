import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class HeavyRescueTests(unittest.TestCase):
    def test_world_to_havok_uses_target_rotation_and_havok_scale(self) -> None:
        from dmfix.core.fixes.simplify import HAVOK_TO_SKYRIM, _world_to_havok

        collision = SimpleNamespace(
            target_linear=(0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            target_translation=(0.0, 0.0, 0.0),
            body_translation=(0.0, 0.0, 0.0),
            body_rotation=(0.0, 0.0, 0.0, 1.0),
        )

        world = np.asarray((2.0, -1.0, 3.0)) * HAVOK_TO_SKYRIM
        self.assertTrue(np.allclose(_world_to_havok(world, collision), (1.0, 2.0, 3.0)))

    def test_certification_failure_reports_the_changed_safety_metric(self) -> None:
        from dmfix.core.fixes.acceptance import simplify_certification_failures

        baseline = {
            "status": "OK",
            "verdict": "VERY HEAVY COLLISION",
            "broken": {"refs": 0},
            "freeze": {"cullVerdict": 0},
            "orientation": {"inverted": 0},
            "winding_cull": {"inverted": 0},
            "degenerate": {"tris": {"count": 0}},
            "ray_status": "ok",
            "fall_through_risk": {"level": "none"},
            "invisible_walls": {"count": 0},
        }
        scan = {
            **baseline,
            "verdict": "OK",
            "invisible_walls": {"count": 19},
        }

        self.assertEqual(
            simplify_certification_failures(baseline, scan),
            ["invisible_walls.count=19>0"],
        )

    def test_rescue_budget_keeps_conservative_path_unchanged(self) -> None:
        from dmfix.core.fixes.simplify import _rescue_budget

        self.assertEqual(_rescue_budget("conservative"), 0)
        self.assertEqual(_rescue_budget("normal"), 6)
        self.assertEqual(_rescue_budget("aggressive"), 6)

    def test_multi_component_safety_rescue_keeps_global_target(self) -> None:
        from dmfix.core.fixes.simplify import _rescue_target

        self.assertEqual(_rescue_target(1500, 3, 13, local_only=True), 1500)
        self.assertEqual(_rescue_target(1500, 3, 45, local_only=False), 200)
        self.assertEqual(_rescue_target(1500, 2, 1, local_only=True), 375)
        self.assertEqual(
            _rescue_target(1429, 1, 13, local_only=False, gentle=True),
            1071,
        )

    def test_local_reserve_distinguishes_bridge_from_large_wall(self) -> None:
        from dmfix.core.fixes.simplify import _local_reserve

        self.assertEqual(_local_reserve(28, 2247), 8)
        self.assertEqual(_local_reserve(117, 8422), 512)
        self.assertEqual(_local_reserve(3, 14589), 64)

    def test_closed_component_winding_is_restored_as_a_whole(self) -> None:
        from dmfix.core.fixes.simplify import _repair_closed_component_winding

        points = np.asarray(
            [
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            ],
            dtype=np.float64,
        )
        source_faces = np.asarray(
            [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)],
            dtype=np.int32,
        )
        inverted_faces = source_faces[:, [0, 2, 1]]

        repaired = _repair_closed_component_winding(
            points,
            inverted_faces,
            points,
            source_faces,
        )

        self.assertTrue(np.array_equal(repaired, source_faces))


if __name__ == "__main__":
    unittest.main()

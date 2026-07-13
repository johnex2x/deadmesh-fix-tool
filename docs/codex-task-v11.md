# Task: v1.1 — rescue the HEAVY-simplification failures

## Problem statement (evidence from real runs)

`tests/fixtures` currently yields 13/17 HEAVY successes. The failures, with root causes:

| file | symptom | root cause |
|---|---|---|
| cf14tabledesk / cf7tablefront | dmscan verdict **OK**, but our acceptance rejects on raw `holes.count` tolerance | our gate is stricter than DeadMesh's own philosophy: its docs state fall-through points are NOT flagged "because simplified hulls and collisionless decor are normal in Skyrim meshes". Raw hole-point counts on a simplified hull are expected to grow. |
| cf2table | decimation produces `INVERTED COLLISION` verdict | fast_simplification occasionally flips orientation of a region; we refuse (correctly) but never try to repair it |
| cf4grand / sm_ornamental_wall_01 | still HEAVY after 3 rounds (e.g. cf4grand 8309->2770 tris, cull 2877->780) | hundreds of tiny connected components (cf4grand: 384); per-component floors dominate, decimation cannot reach the target |

Also observed: results near the holes threshold flip between runs (dmscan sampling variance),
so the raw-count gate is nondeterministic at the boundary.

## Changes

1. `src/dmfix/core/fixes/acceptance.py` — replace the raw holes gate in BOTH
   `nothing_got_worse` and `simplify_scan_is_acceptable`:
   - drop: `holes.count <= baseline*1.25+10`.
   - add (when both ray statuses are "ok"):
     * `scan.fall_through_risk.level` must not be worse than baseline (none < low < ... —
       inspect actual values emitted by dmscan on the fixtures; treat unknown levels
       conservatively as worse),
     * `scan.fall_patch.sites <= baseline.fall_patch.sites` allow equal,
     * `scan.holes_enclosed <= baseline.holes_enclosed`,
     * keep `invisible_walls.count` not worse (that's the "invisible wall" defect DeadMesh DOES flag),
     * keep everything else as is.
   - cite the DeadMesh doc line in a comment (DOCUMENTATION.md: Ray-Cast pass, "fall-through
     points are listed but not flagged...").
2. `src/dmfix/core/fixes/simplify.py` — post-decimation orientation repair:
   after simplifying each component, compare its faces' normals against the nearest source
   faces (source = that component's input triangles; nearest by centroid, sample up to ~50
   faces): if the majority disagrees in sign, flip the whole component's winding before
   encoding. Never flip individual faces. This must run before MOPP compile.
3. `src/dmfix/core/fixes/simplify.py` — small-island consolidation:
   islands with fewer than 16 source triangles are replaced by their 3D convex hull
   (pure-Python quickhull — no new deps; handle coplanar/degenerate islands by falling back to
   the original island unchanged), with the island's material. Hull output winding must be
   outward (verify with signed volume; flip if negative). Larger islands keep the existing
   decimation path. Apply in all strengths.
4. Round-3 fallback for many-island meshes: if after round 3 the scan is still HEAVY and the
   mesh has > 100 components, retry once more with hull-replacement threshold raised to 64
   source triangles.
5. Tests `tests/test_simplify.py`: keep the same table format; the success criterion becomes
   >= 16/17. Print which acceptance dimension failed for any remaining failure. All other test
   files must still pass unchanged (test_other_fixes uses acceptance.nothing_got_worse — verify
   its synthetic degenerate/winding flows still pass with the new gate).

## Constraints

- Only touch: acceptance.py, simplify.py, test_simplify.py. No new dependencies. No git.
- The dmscan verdict gates (no HEAVY/CRASH/HANG, broken.refs == 0, cullVerdict) are untouchable.
- The `dmscan --vs` pipeline gate stays as the final inversion safety net (pipeline side,
  do not modify pipeline).
- Fail closed as before for anything that still cannot be certified.

## Verification

- `./.venv/Scripts/python tests/test_simplify.py` — table + >= 16/17.
- `./.venv/Scripts/python tests/test_other_fixes.py` — 5 passed.
- `./.venv/Scripts/python tests/test_mopp_rebuild.py` — 2 passed.

## Report

Per-file table before/after, which change rescued which file, any file still failing and the
precise dimension, verbatim test output, open risks.

# Task: HEAVY-collision fix — simplify collision geometry and rebuild chunks + MOPP

## Background

17 of the 18 fixtures in `tests/fixtures/` are flagged HEAVY or VERY HEAVY COLLISION by dmscan:
the collision mesh is nearly as dense as the visible model, so one actor-sized physics query
returns hundreds-thousands of triangles (see `freeze.cullWorst` / `cullVerdict` in
`tests/fixtures_baseline.jsonl`; cullVerdict 2 = VERY HEAVY, 1 = HEAVY). The fix: decimate the
collision geometry (NOT the visible mesh) to a low-poly approximation, rebuild the
bhkCompressedMeshShapeData chunks and the MOPP bytecode, and splice both blocks back into the
file. Everything else stays byte-identical.

## Existing infrastructure (reuse, do not duplicate)

- `src/dmfix/core/nif_io.py` — `NifFileLayout` (block-level binary reader), `replace_block`
  (extend it or add a multi-block variant — replacing TWO blocks shifts offsets, so compute
  carefully from the ORIGINAL layout), `locate_collisions`, `read_mopp`.
- `src/dmfix/core/fixes/mopp_rebuild.py` — `decode_compressed_mesh` gives
  (vertices Havok-space, triangles, output_ids, radius, bits) for the current file. You will
  need per-triangle MATERIAL too: extend the decoder to also return, per triangle, the
  HavokMaterial u32 (chunk material index -> ChunkMaterials table entry; big tris carry their
  own material field). Keep the existing return shape backward-compatible (add fields).
- `vendor/pyn/mopp_compiler.py` (`compile_mopp`), `vendor/mopp_verifier.py`,
  `vendor/pyn/mesh_segment.py` (`segment_mesh`), `vendor/pyn/tri_strip.py` (`stripify`) — chunking
  and stripping helpers; `vendor/pyn/pynifly.py` `bhkCompressedMeshShape.Create` (lines ~804-1010)
  documents the exact chunk encoding, quantization (u16, *1000, per-chunk translation, 65.535
  unit max extent) and shape-key layout — mirror it in a pure-binary writer.
- `.venv/Scripts/python` has `fast_simplification` (quadric decimation; keeps face orientation)
  and `numpy`.
- Judge: `dmscan.exe --json <file>` (path in tests/test_mopp_rebuild.py).

## Deliverables

1. `src/dmfix/core/fixes/simplify.py`:
   - `simplify_collision(input_path, output_path, strength="normal") -> SimplifyResult`
   - Steps:
     a. decode collision mesh + per-triangle materials.
     b. drop degenerate triangles (zero area / repeated vertex index) up front.
     c. group triangles by material; decimate each group independently with
        `fast_simplification.simplify` (preserves winding); never let a group drop below 8
        triangles or collapse to zero if it had geometry.
     d. reduction target: derive from baseline `freeze.cullWorst` — aim so the worst query
        returns well under the HEAVY threshold. Empirical iteration: try reduction to
        N_target = min( max(200, tris*0.25), 1500 ) triangles overall for "normal"
        (scale 0.5x for "conservative", 0.15x аggressive floor for "aggressive"), then rescan
        with dmscan; if still cullVerdict >= 1, halve the target and retry (max 3 rounds).
     e. rebuild bhkCompressedMeshShapeData binary payload: materials table, chunks (segment via
        `segment_mesh`, split by material, quantize, stripify), bigVerts/bigTris only if a chunk
        would exceed u16 quantization extent (65.535 Havok units) — in that case put those
        triangles in bigTris with their material. Set welding arrays empty ONLY if the format
        permits (count 0); preserve header fields (error, AABB recomputed from new geometry,
        bit widths/masks copied from original), weldingType/materialType bytes preserved.
     f. compile new MOPP with the real shape keys of the NEW chunk layout; verify with
        mopp_verifier (100% surface reachability required).
     g. splice BOTH blocks (data + mopp) into the file; all other blocks byte-identical.
   - The bhkCompressedMeshShape block itself (56 bytes) should not need changes; assert its
     payload is preserved.
2. `tests/test_simplify.py` (plain-script runnable, same style as test_mopp_rebuild.py):
   - run simplify on ALL 17 HEAVY fixtures (every baseline record with cullVerdict >= 1),
     output to `tmp/simplified/<name>.nif`.
   - per file assert: dmscan verdict has no HEAVY/VERY HEAVY/CRASH/HANG; `broken.refs == 0`;
     no regression vs baseline on orientation.inverted, winding_cull.inverted (allow equal),
     degenerate counts; holes/invisible_walls compared only when baseline `ray_status == "ok"`
     AND scan ray_status == "ok" (holes may legitimately grow slightly on simplified hulls —
     allow up to baseline + 25% + 10 points before failing; document this tolerance).
   - files that fail after 3 rounds: no output file, recorded in a `failures` list; the test
     PASSES if >= 13/17 succeed and prints a per-file summary table (name, old cullWorst,
     new cullWorst, tri count old->new, rounds, verdict).
   - also re-run the stage-1 test to prove no regression: `tests/test_mopp_rebuild.py` must
     still pass.

## Constraints

- Do not modify visible-mesh geometry blocks — only bhkCompressedMeshShapeData + bhkMoppBvTreeShape.
- Winding preservation is critical (inverted collision = player falls through). After
  decimation, verify each material group's signed orientation is consistent with its source
  (e.g. compare average normal agreement of nearest original faces, or rely on
  fast_simplification's orientation guarantee + dmscan winding_cull check).
- No new third-party deps beyond what's installed.
- Only touch `src/dmfix/core/fixes/simplify.py`, minor extensions to
  `mopp_rebuild.py`/`nif_io.py` (backward-compatible), and `tests/test_simplify.py`.
  Do not run git commands.
- If a hard blocker appears (format detail that cannot be verified, welding turns out
  mandatory, etc.), STOP and report rather than guessing.

## Final report format

- Per-file result table (17 rows): success/fail, rounds, tri counts, cullWorst old->new,
  final verdict, any tolerance used.
- Any deviation from the encoding documented in pynifly Create().
- Welding decision and evidence dmscan accepts it.
- Test output verbatim (both test files).
- Assumptions / open risks.

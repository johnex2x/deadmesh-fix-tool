# Task: remaining fix categories — DEGENERATE, INVERTED, orphaned collision blocks

## Background

Stages 1-2 delivered: surgical MOPP rebuild (`fixes/mopp_rebuild.py`), collision decode with
per-triangle materials, pure-binary chunk re-encode + decimation (`fixes/simplify.py`,
`_encode_compressed_mesh`), dmscan wrapper (`core/scanner.py`, see `FixCategory`). This stage
adds the three remaining categories. **There are no real specimens in `tests/fixtures/`** for
these defects, so tests must SYNTHESIZE broken files with our own encoder, prove dmscan flags
them, fix them, and prove dmscan then reports them clean.

## Deliverables

1. `src/dmfix/core/fixes/degenerate.py` — `fix_degenerate(input_path, output_path) -> result`:
   - decode mesh + materials, drop degenerate triangles (reuse `_drop_degenerate` from
     simplify.py — move it to a shared location if cleaner), re-encode chunks + MOPP via the
     existing binary writers, splice, verify with dmscan: `degenerate.tris.count == 0`,
     no new defects (same acceptance rules as simplify's `_scan_is_acceptable`, including the
     ray_status gating and CRASH/HEAVY absence — but do NOT require HEAVY to vanish if the
     baseline already had it; a degenerate fix must not be blocked by an orthogonal pre-existing
     verdict. Factor the acceptance check so each fix compares only its own dimension plus
     "nothing got worse").
2. `src/dmfix/core/fixes/winding.py` — `fix_inverted(input_path, output_path) -> result`:
   - use dmscan's own analysis to decide what to flip: run `scan_file`, read
     `orientation.bad_components` (list of inverted component descriptors) and/or
     `winding_cull` fields. Flip triangle winding (swap two indices) for the triangles in the
     components dmscan marks inverted. Mapping dmscan's component report to our decoded
     triangles: components are connected collision islands — reuse
     `_connected_face_components`; match by centroid/bbox against dmscan's `at`/component data.
     If the mapping is ambiguous for a given file, fail closed with a clear reason (report-only).
   - if dmscan reports whole-shape inversion (all or nearly all triangles), flip everything.
   - re-encode + MOPP + splice + verify: `orientation.inverted == 0` and
     `winding_cull.inverted` strictly reduced, nothing else worse.
3. `src/dmfix/core/fixes/orphan.py`:
   - first, INVESTIGATE: does the vendored NiflyDLL export anything usable for deleting a block
     or saving a cleaned file (grep `vendor/pyn/niflydll.py` for remove/delete/prune)? Report
     what exists.
   - implementing general block deletion in raw binary requires rewriting every block ref
     (indices shift) — only safe with full type schemas, which we do not have. Therefore:
     implement `remove_orphan_collision(input_path, output_path)` ONLY for the safe case —
     orphan block(s) whose removal does not disturb other refs, i.e. a contiguous run at the
     END of the block table AND no ref anywhere pointing at-or-past them. A conservative ref
     scan: any i32 field in any block payload equal to the orphan index is treated as a
     potential ref (false positives acceptable -> fail closed). Otherwise return a
     "report-only" result explaining why (user advised to remove in NifSkope, per DeadMesh's
     own guidance that orphans are harmless in game).
4. `tests/test_other_fixes.py` (plain-script style, like the others):
   - synthesize from a clean simplified fixture (e.g. run simplify on `mush3.nif` first, or use
     a stage-2 output): using `_encode_compressed_mesh`, produce:
     a. a copy with ~20 degenerate triangles injected (duplicate-vertex tris) -> assert dmscan
        flags degenerate count > 0 -> fix_degenerate -> assert count == 0, nothing worse.
     b. a copy with ALL triangle windings flipped -> assert dmscan reports INVERTED / high
        winding_cull.inverted -> fix_inverted -> assert clean.
     c. a copy with one island's winding flipped (pick the largest connected component) ->
        same flow; if dmscan does not flag the partial inversion, document that and keep the
        full-inversion test as the gate.
     d. orphan: append a duplicate bhkCompressedMeshShapeData block at the END of a file
        (header block-type/size tables updated, no refs to it) -> assert dmscan reports
        orphan_collisions > 0 (verify this is the field that reacts; if dmscan does not flag
        our synthetic orphan, report and mark the removal path as untestable-but-implemented) ->
        remove_orphan_collision -> assert file byte-identical to the pre-orphan original.
   - all existing tests must still pass (test_mopp_rebuild, test_simplify may be slow — run it
     once at the end; if runtime is a concern, run only its cheap assertions… no: run it fully).
5. Shared refactor allowed (keep it minimal): a common `acceptance.py` for the per-category
   "nothing got worse" scan comparison, used by simplify/degenerate/winding. Update simplify.py
   to use it WITHOUT changing its behavior (test table must stay identical: 13/17 same names).

## Constraints

- Only binary-splice writes (existing infra). No pynifly DLL writes to output files.
- Fail closed everywhere: unsupported layout / ambiguous mapping -> no output file + reason.
- Do not run git commands. Do not touch GUI/CLI/pipeline files.

## Final report format

- Per-deliverable summary; DLL investigation result for orphan handling.
- dmscan evidence for each synthetic defect (flagged) and each fix (clean) — key JSON fields.
- Verbatim test output of ALL test files.
- Assumptions / open risks / anything untestable.

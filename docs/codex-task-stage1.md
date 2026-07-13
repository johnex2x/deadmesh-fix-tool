# Task: CRASH-RISK fix pipeline — rebuild broken MOPP bytecode in Skyrim SE .nif collision

## Background

DeadMesh (a MOPP collision validator for Skyrim SE meshes) flags `tests/fixtures/sm_tower_roof_1.nif`
as **CRASH RISK**: "The MOPP tree contains an illegal opcode (invalidOpcode-INT3 op=0x88 @ ip=0x109f)".
The collision *geometry* (bhkCompressedMeshShape chunks) is intact; only the MOPP bytecode
(BVH search tree inside bhkMoppBvTreeShape) is corrupt. The fix: recompile the MOPP bytecode
from the existing collision triangles and write it back, changing NOTHING else.

## Environment

- Project root: this repo. Python venv: `.venv/Scripts/python` (3.11, has numpy/lz4).
- `vendor/pyn/` — vendored PyNifly python package (works headless; `sys.path.insert(0,'vendor')`
  then `from pyn.pynifly import NifFile`). NiflyDLL at `vendor/NiflyDLL.dll` (auto-resolved by
  `pyn/niflydll.py`).
- Key vendored modules:
  - `vendor/pyn/mopp_compiler.py` — `compile_mopp(verts, triangles, radius, output_ids) -> (bytes, origin, scale)`
    and `disassemble_mopp(...)`. Verts in **Havok space**, output_ids are the per-triangle
    uint32 "shape keys" the MOPP leaves must return.
  - `vendor/mopp_verifier.py` — walks MOPP bytecode with sample points, verifies reachability /
    correctness / completeness. Use it as an internal gate.
  - Reference for reading chunk internals: original PyNifly repo has `scripts/dump_collision.py`
    at `C:/Users/Johnex2x/Documents/DeadMesh-Fix-Tool/PyNifly/scripts/dump_collision.py` (READ-ONLY
    reference, do not modify) — it decodes bhkCompressedMeshShapeData chunks (quantized verts,
    strips, flat tris, per-chunk transforms) and disassembles MOPP, showing exactly how
    triangles map to MOPP output IDs (shape keys: `((chunk_idx+1) << bitsPerWIndex) | (winding << bitsPerIndex) | index_pos`).
  - `vendor/pyn/pynifly.py` lines ~592-1052: `bhkMoppBvTreeShape` (incl. `mopp_data` getter and
    `Create()`), `bhkCompressedMeshShape` (`vertices`, `triangles`, `material_ids` getters,
    `Create()` which shows the exact output-id encoding used by the engine).
- External judge: `dmscan.exe` at
  `C:/Users/Johnex2x/Documents/DeadMesh-Fix-Tool/DeadMesh - MOPP Collision Validator/dmscan.exe`.
  - `dmscan.exe --json <file.nif>` prints one JSON record to stdout (verdict + all defect fields).
  - Baseline scan of all fixtures: `tests/fixtures_baseline.jsonl`.

## Deliverables

1. `src/dmfix/core/nif_io.py` — headless NIF collision access layer:
   - locate all collision objects in a nif (root node and child nodes), returning for each:
     target node name, rigid body block, shape chain (bhkMoppBvTreeShape -> child shape type).
   - a minimal **binary NIF block-level reader/patcher** (see approach below): parse the SE NIF
     header (version 20.2.0.7, user 12, stream 100), block types/sizes table, locate a given
     block's byte range, and splice a replacement block payload (updating the header's block-size
    entry). Nothing else in the file may change.
2. `src/dmfix/core/fixes/mopp_rebuild.py` — the fix:
   - decode the existing bhkCompressedMeshShapeData chunks **directly from the binary** (or via
     NiflyDLL getters if byte-identical results are proven) to obtain every triangle the engine
     can decode plus its exact MOPP output ID (shape key) — strips AND flat tris, winding
     included, per-chunk translation applied, bigTris/bigVerts if present.
   - recompile MOPP via `compile_mopp` with those (verts_havok, tris, output_ids).
   - verify internally with `vendor/mopp_verifier.py` logic (reachability 100% on surface samples).
   - splice the new MOPP bytes into the bhkMoppBvTreeShape block payload (moppDataSize, origin,
     scale, buildType=1 preserved/updated correctly), write result to an output path.
3. `tests/test_mopp_rebuild.py` — pytest (or plain script runnable via venv python):
   - fix `tests/fixtures/sm_tower_roof_1.nif` -> `tmp/sm_tower_roof_1.nif`
   - assert: dmscan --json on the fixed file has `status != "BROKEN"`, `verdict` no longer
     contains "CRASH", `broken.refs == 0`, and no NEW defects vs the baseline record for this
     file in `tests/fixtures_baseline.jsonl` (compare: orientation.inverted, holes.count,
     degenerate counts, winding_cull.inverted, orphan fields must not get worse).
   - assert: every byte of the output file outside the bhkMoppBvTreeShape block (and the one
     block-size header entry) is identical to the input (this proves geometry untouched).

## Approach constraints (agreed with the user; do not deviate without flagging)

- **Preferred approach: direct binary patch.** Only the MOPP bytecode inside the
  bhkMoppBvTreeShape block (and its size field in the NIF header block-size table) changes.
  Geometry chunks stay byte-identical. This is deliberately chosen over a full pynifly
  load->rebuild->save round trip to guarantee zero collateral changes.
- If the binary-patch route hits a hard blocker, STOP and report — do not silently fall back.
- bhkMoppBvTreeShape block layout (NifSkope nif.xml "bhkMoppBvTreeShape", SE): 
  shape ref (int32), 3x uint32 unused, float shapeScale, uint32 moppDataSize,
  Vector3 origin + float scale (this Vector4 = "Offset"; scale = 254*256*256/largest_dim),
  byte buildType (SE only; 1 = BUILT_WITHOUT_CHUNK_SUBDIVISION), then moppDataSize bytes of code.
  VERIFY this against the actual bytes before trusting it (cross-check moppDataSize/origin/scale
  read from binary vs `bhkMoppBvTreeShape.mopp_data` from the DLL on a healthy fixture, e.g.
  `tests/fixtures/t_crystal4vanilla.nif`).
- Havok<->Skyrim scale: 1 Havok unit = 69.99 Skyrim units. Chunk data is in Havok space —
  compile_mopp must receive Havok-space coordinates exactly as stored (do NOT rescale).
- The output_ids passed to compile_mopp must be the engine's real shape keys derived from the
  ACTUAL chunk layout in this file (bitsPerIndex/bitsPerWIndex/masks read from the
  bhkCompressedMeshShape block), not sequential renumbering.
- Code style: plain Python, type hints, no new third-party deps. Comments only where the binary
  format demands explanation.

## Verification commands

```
./.venv/Scripts/python -m pytest tests/test_mopp_rebuild.py -x -q   # or plain script
cd "C:/Users/Johnex2x/Documents/DeadMesh-Fix-Tool/DeadMesh - MOPP Collision Validator" && ./dmscan.exe --json <abs path to tmp/sm_tower_roof_1.nif>
```

## Final report format

- What was implemented, file by file.
- The dmscan verdict JSON for the fixed file (verbatim).
- Byte-diff summary: which byte ranges changed.
- mopp_verifier results (reachability %, false-positive rate old vs new if measurable).
- Assumptions made; anything you could not verify; open risks.

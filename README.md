# DeadMesh Fix Tool

**Automated collision fixer for meshes flagged by [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829) (by TesFantom).**

> [繁體中文說明](README.zh-TW.md)

DeadMesh finds broken Havok collision in Skyrim SE/AE/VR `.nif` files — collision that crashes
the game, freezes it, or tanks your framerate. It deliberately does not repair anything.
**DeadMesh Fix Tool is the unofficial companion that does the repair step automatically**, so
you don't have to walk the manual Blender + PyNifly + NifSkope route for every flagged mesh.

This is an unofficial companion tool. It is not made by, or endorsed by, TesFantom.

## What it fixes

| DeadMesh verdict | What the tool does |
|---|---|
| **CRASH RISK / HANG RISK / BROKEN COLLISION** | Rebuilds the corrupt MOPP search tree from the intact collision geometry. The geometry itself is untouched — every byte outside the MOPP block stays identical. |
| **HEAVY / VERY HEAVY COLLISION** | Decimates the over-dense collision mesh (per-material, shape-preserving), rebuilds the chunks and the MOPP. Strength is selectable (conservative / normal / aggressive). |
| **DEGENERATE COLLISION** | Removes zero-area / collapsed collision triangles and rebuilds the MOPP. |
| **INVERTED COLLISION** | Flips the winding of inverted collision shells (the defect that makes the player fall through). Ambiguous cases are refused rather than guessed. |
| **Orphaned collision blocks** | Removes unreferenced leftover collision blocks when it is provably safe; otherwise tells you to do it in NifSkope. |
| **ORPHAN MOPP** | Cannot be fixed automatically (the geometry was stripped from the file); reported for manual repair. |

## The safety contract

1. **Your originals are never modified.** Fixed meshes are written as loose files to a separate
   mesh output folder (default `<mod>\DeadMesh-Fixed\Meshes`), mirroring the paths below `meshes\...` so you can
   drop them into your Data folder or a mod-manager mod. BSA archives are read, never written.
2. **DeadMesh is the judge, not us.** After every fix the tool re-scans the result with
   DeadMesh's own `dmscan` engine. A file is only written when the original defect is gone
   **and nothing else got worse** (no new inversion, no new holes beyond tolerance, no new
   crash class — including dmscan's `--vs` winding-regression check).
3. **Fail closed.** Anything that cannot be certified is *not* written; it appears in the
   report with the reason and the manual-fix route instead.

## Requirements

- Windows 10/11, 64-bit
- [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829) installed
  (the tool asks for its folder on first run — it needs `dmscan.exe`)

## Usage (GUI)

1. Run `DeadMeshFixTool.exe`. On first run, point it at your DeadMesh folder.
2. Pick the mod folder to scan (loose files and BSA archives are both covered; loose files
   override BSA copies, exactly like the game does).
3. Tick the fix categories you want (all on by default) and pick a simplification strength.
4. **Scan**, review the list, **Fix**.
5. Everything in the output folder passed certification. Failures are listed with reasons in
   the results table and in `deadmesh-fix-report.txt` / `.json`.

## Usage (command line)

Use `dmfix.exe` (the console launcher in the same folder; `DeadMeshFixTool.exe` is the
windowed GUI launcher and prints nothing to a terminal):

```
dmfix.exe <mod folder> [--deadmesh <dir>] [--out <dir>]
          [--fix crash,heavy,degenerate,inverted,orphan_blocks]
          [--strength conservative|normal|aggressive] [--no-bsa]
```

Exit code 0 = everything fixable was fixed; 1 = some files failed/unfixable; 2 = usage error.

## Manual fallback for files the tool refuses

The tool's refusal list is your to-do list for the classic manual route:

1. Import the `.nif` into Blender with the [PyNifly add-on](https://github.com/BadDogSkyrim/PyNifly).
2. Fix or rebuild the collision geometry (a collider only needs to approximate the silhouette —
   low-poly is correct, don't use the render mesh).
3. Assign the correct `SKY_HAV_MAT_*` vertex group, export with PyNifly.
4. Compare in NifSkope, then verify with DeadMesh.

## Building from source

```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m PyInstaller build.spec
```

Tests (fixture `.nif` meshes are not included in this repository, since they are third-party mod
assets and not ours to redistribute — place your own test meshes in `tests/fixtures/` to run
these; dmscan.exe must be present in a sibling `DeadMesh - MOPP Collision Validator` folder):

```
.venv\Scripts\python tests\test_mopp_rebuild.py
.venv\Scripts\python tests\test_simplify.py
.venv\Scripts\python tests\test_other_fixes.py
.venv\Scripts\python tests\test_bsa.py
.venv\Scripts\python tests\test_gui_logic.py
```

## Credits & license

- **TesFantom** — [DeadMesh - MOPP Collision Validator](https://www.nexusmods.com/skyrimspecialedition/mods/181829),
  the detection engine this tool is built around, and the reverse-engineering research behind it.
- **BadDogSkyrim** — [PyNifly](https://github.com/BadDogSkyrim/PyNifly). This tool vendors
  PyNifly's `pyn` package (NIF I/O, MOPP compiler/verifier, NiflyDLL) under GPL-3.0.
- BSA reading is an original clean-room implementation of the public BSA v104/v105 format.

DeadMesh Fix Tool is free software under the **GNU General Public License v3.0** (see
`LICENSE`). Source code: <https://github.com/johnex2x/deadmesh-fix-tool>

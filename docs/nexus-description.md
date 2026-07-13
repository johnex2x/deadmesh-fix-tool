# Nexus page draft — DeadMesh Fix Tool

> Suggested category: Utilities / Modders Resources. Suggested tags: Utility, Crash Fix.

---

## Short description (255 chars)

Automated collision fixer for meshes flagged by DeadMesh - MOPP Collision Validator. Rebuilds
corrupt MOPP trees (CTD fixes), simplifies over-dense collision (FPS fixes), and only outputs
files re-certified clean by DeadMesh itself. GUI + CLI.

## Description

**DeadMesh finds the broken collision. This tool fixes it.**

[url=https://www.nexusmods.com/skyrimspecialedition/mods/181829]DeadMesh - MOPP Collision
Validator[/url] (by TesFantom) scans your load order and names the exact meshes that can crash,
freeze, or lag your game. It deliberately repairs nothing. Until now the repair step meant
Blender + PyNifly + NifSkope, one mesh at a time.

DeadMesh Fix Tool automates that step:

[list]
[*][b]CRASH RISK / HANG RISK[/b] — rebuilds the corrupt MOPP search tree from the intact
collision geometry. Every byte outside the MOPP block stays identical to the original.
[*][b]HEAVY / VERY HEAVY COLLISION[/b] — decimates the over-dense collision (per-material,
shape-preserving) so a physics query stops returning half the mesh. Strength selectable.
[*][b]DEGENERATE COLLISION[/b] — strips zero-area triangles and rebuilds.
[*][b]INVERTED COLLISION[/b] — flips inside-out collision shells (the "player falls through"
defect). Ambiguous cases are refused, never guessed.
[/list]

[b]The safety contract[/b]

[list=1]
[*]Originals are never touched. Fixes are written as loose files to a separate folder,
mirroring the meshes\ tree. BSAs are read, never repacked.
[*]DeadMesh is the judge. Every fix is re-scanned with dmscan; a file is only written when the
defect is gone AND nothing else got worse (winding regression, new holes, new crash class).
[*]Fail closed. Anything that can't be certified is reported with a reason and the manual-fix
route instead of being written.
[/list]

[b]Requirements[/b]: DeadMesh - MOPP Collision Validator (hard requirement — the tool drives
its dmscan.exe). Windows 10/11 64-bit. Nothing else to install.

[b]Usage[/b]: run the EXE, point it at your DeadMesh folder once, pick a mod folder, Scan, Fix.
Command-line mode for batch/scripting: run with arguments (see README).

[b]Interface[/b]: English / 繁體中文.

[b]Credits[/b]
[list]
[*]TesFantom — DeadMesh - MOPP Collision Validator, the detection engine this tool is built
around. This is an unofficial companion; not made by or endorsed by TesFantom.
[*]BadDogSkyrim — PyNifly (GPL-3.0), whose NIF I/O and MOPP compiler this tool builds on.
[/list]

[b]Source[/b]: GPL-3.0, full source on GitHub: <link>. Bug reports welcome — attach the
deadmesh-fix-report.txt from your output folder.

## Permissions block

- Open source (GPL-3.0). Anyone may fork/redistribute under the same license with credit.
- Not affiliated with TesFantom or BadDogSkyrim.

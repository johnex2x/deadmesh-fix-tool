# Task: PySide6 GUI for DeadMesh Fix Tool

## Product definition (agreed with the user — do not extend scope)

A downstream companion to DeadMesh - MOPP Collision Validator (by TesFantom). The GUI wraps
the existing pipeline: pick a mod folder -> scan (via dmscan) -> tick fix categories -> Fix ->
per-file results. It must NOT duplicate DeadMesh features (no 3D viewer, no verdict browser
beyond what the fix run needs). Originals are never modified; fixes land in an output folder
as loose files.

## Existing backend (use as-is; do not modify core/)

- `dmfix.core.pipeline`: `PipelineOptions(deadmesh_dir, output_dir, categories, strength,
  include_bsa)`, `run_pipeline(target_folder, options, progress_cb) -> RunReport`,
  `CATEGORY_ORDER`. progress_cb(stage:str, current:int, total:int, message:str); stages are
  "scan", "extract", "fix".
- `dmfix.core.report`: `RunReport` (`.results: list[FileResult]`, `.counts()`, `.save(folder)`),
  `FileResult` (source, relative_path, categories, outcome: Outcome, reason, output_path,
  verdict_before, verdict_after, detail). Outcome enum: FIXED/FAILED/UNFIXABLE/SKIPPED/ERROR.
- `dmfix.core.scanner`: `FixCategory` enum (crash, heavy, degenerate, inverted, orphan_blocks,
  unfixable), `find_deadmesh_dir()`, `DmScanError`.
- Entry point `dmfix.main:main` already dispatches: no argv -> `dmfix.gui.main_window.run_gui()`.

## Deliverables (only these files + tests)

1. `src/dmfix/gui/i18n.py` — string table module:
   - `tr(key: str) -> str`; `set_language("en" | "zh-TW")`; all UI strings in one dict with
     English and Traditional Chinese values. Default English.
2. `src/dmfix/gui/settings.py` — persisted app settings:
   - JSON at `%APPDATA%/DeadMesh Fix Tool/settings.json`: deadmesh_dir, language,
     last_target_folder, strength, categories, include_bsa.
   - `load() -> Settings`, `save(settings)`; robust to missing/corrupt file.
3. `src/dmfix/gui/main_window.py` — the app:
   - `run_gui() -> int` creates QApplication, shows MainWindow, returns exec code.
   - First-run flow: if settings.deadmesh_dir missing or lacks dmscan.exe: show a modal setup
     dialog — short explanation + folder picker + link-style hint to the DeadMesh Nexus page
     (plain text is fine); try `find_deadmesh_dir()` as the prefilled suggestion. Refuse to
     continue without a valid folder (offer Quit).
   - Main window layout (keep it simple, standard QWidgets, no QML):
     a. Row: target mod folder picker (line edit + Browse), "Scan" button.
     b. Row: output folder picker (default `<target>\DeadMesh-Fixed`), auto-updates when the
        target changes unless the user edited it manually.
     c. Group "Fix categories": five checkboxes (crash, heavy, degenerate, inverted,
        orphan_blocks — labels human-readable via i18n, e.g. "Crash risk (broken MOPP)"),
        all checked by default; plus strength combo (conservative/normal/aggressive) enabled
        only while "heavy" is checked; "include BSA archives" checkbox (default on).
     d. "Fix" button (disabled until a scan found fixable items), progress bar + status line
        driven by progress callbacks.
     e. Results table (QTableWidget): columns Status | Mesh | Before -> After | Categories |
        Reason. Status cell colored: green FIXED, red FAILED, orange UNFIXABLE, grey
        SKIPPED/ERROR. Row count label: "Fixed X, failed Y, ...".
     f. Bottom row: "Open output folder" (QDesktopServices), "Save report" (already auto-saved
        by pipeline — this button just opens the report .txt), language combo (en / 繁體中文;
        applying re-translates all visible widgets), About box (GPL-3.0, credits TesFantom's
        DeadMesh + BadDogSkyrim's PyNifly, unofficial companion disclaimer).
   - Scan phase: run `collect_work_items`-equivalent via the pipeline? NO — the pipeline runs
     scan+fix in one call. For the GUI's two-step UX (scan first, show what WOULD be fixed,
     then Fix): import `collect_work_items` and `PipelineOptions` from pipeline; run it in a
     worker thread; display found items (relative_path, verdict, categories) as pending rows;
     REMEMBER the returned temp dir must be cleaned (shutil.rmtree) if the user re-scans or
     quits without fixing. For the Fix phase call `run_pipeline` (it re-scans internally; the
     small duplicate cost is acceptable and keeps the backend API untouched).
   - Threading: QThread worker objects with signals (progress, finished, error). The UI must
     stay responsive; Fix button disabled while running; window close during a run asks for
     confirmation and terminates cleanly (worker checks an abort flag between files is NOT
     available in the backend — just warn "a run is in progress").
   - All user-visible strings through i18n.tr().
4. `tests/test_gui_logic.py` — headless logic tests only (no QApplication display needed; use
   QApplication with `-platform offscreen` or test pure functions): settings round-trip,
   i18n completeness (every key has en and zh-TW), output-folder auto-derivation logic.

## Constraints

- PySide6 only (installed in .venv). No new dependencies.
- Type hints; no prints (logging ok).
- Traditional Chinese (zh-TW), not simplified — the user is a zh-TW speaker.
- Do not touch src/dmfix/core/, cli.py, main.py, or existing tests. Do not run git commands.
- Keep visual styling minimal/native; a small dark-neutral QSS is fine but optional.

## Verification

- `./.venv/Scripts/python tests/test_gui_logic.py` passes.
- `PYTHONPATH=src ./.venv/Scripts/python -c "from dmfix.gui.main_window import run_gui"` imports
  cleanly.
- Brief manual smoke instructions in the report (I will run the GUI interactively afterwards).

## Final report format

- File-by-file summary; screenshots not required.
- Any UX decision you made that the spec left open.
- Verbatim test output. Assumptions / open risks.

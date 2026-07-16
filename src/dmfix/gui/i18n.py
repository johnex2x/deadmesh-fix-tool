"""Centralized English and Traditional Chinese UI strings."""
from __future__ import annotations


STRINGS: dict[str, dict[str, str]] = {
    "app_title": {"en": "DeadMesh Fix Tool", "zh-TW": "DeadMesh 修復工具"},
    "setup_title": {"en": "DeadMesh setup", "zh-TW": "DeadMesh 設定"},
    "setup_explanation": {
        "en": "Select the DeadMesh installation folder containing dmscan.exe.",
        "zh-TW": "請選擇包含 dmscan.exe 的 DeadMesh 安裝資料夾。",
    },
    "setup_hint": {
        "en": (
            'DeadMesh on Nexus Mods: <a href="https://www.nexusmods.com/skyrimspecialedition/mods/181829">'
            "https://www.nexusmods.com/skyrimspecialedition/mods/181829</a>"
        ),
        "zh-TW": (
            'Nexus Mods 上的 DeadMesh：<a href="https://www.nexusmods.com/skyrimspecialedition/mods/181829">'
            "https://www.nexusmods.com/skyrimspecialedition/mods/181829</a>"
        ),
    },
    "deadmesh_folder": {"en": "DeadMesh folder", "zh-TW": "DeadMesh 資料夾"},
    "browse": {"en": "Browse...", "zh-TW": "瀏覽..."},
    "save_continue": {"en": "Save and continue", "zh-TW": "儲存並繼續"},
    "quit": {"en": "Quit", "zh-TW": "離開"},
    "invalid_deadmesh": {
        "en": "That folder does not contain dmscan.exe.",
        "zh-TW": "該資料夾不包含 dmscan.exe。",
    },
    "select_deadmesh_folder": {
        "en": "Select DeadMesh folder",
        "zh-TW": "選擇 DeadMesh 資料夾",
    },
    "target_folder": {"en": "Target mod folder", "zh-TW": "目標模組資料夾"},
    "output_folder": {"en": "Output folder", "zh-TW": "輸出資料夾"},
    "select_target_folder": {
        "en": "Select target mod folder",
        "zh-TW": "選擇目標模組資料夾",
    },
    "select_output_folder": {
        "en": "Select output folder",
        "zh-TW": "選擇輸出資料夾",
    },
    "scan": {"en": "Scan", "zh-TW": "掃描"},
    "fix_categories": {"en": "Fix categories", "zh-TW": "修復類別"},
    "category_crash": {
        "en": "Crash risk (broken MOPP)",
        "zh-TW": "當機風險（損壞的 MOPP）",
    },
    "category_heavy": {
        "en": "Heavy collision",
        "zh-TW": "過重碰撞網格",
    },
    "category_degenerate": {
        "en": "Degenerate collision",
        "zh-TW": "退化碰撞三角形",
    },
    "category_inverted": {
        "en": "Inverted collision",
        "zh-TW": "反向碰撞面",
    },
    "category_orphan_blocks": {
        "en": "Orphaned collision blocks",
        "zh-TW": "孤立碰撞區塊",
    },
    "category_unfixable": {
        "en": "Manual repair required",
        "zh-TW": "需要手動修復",
    },
    "strength": {"en": "Strength", "zh-TW": "簡化強度"},
    "strength_conservative": {"en": "Conservative", "zh-TW": "保守"},
    "strength_normal": {"en": "Normal", "zh-TW": "一般"},
    "strength_aggressive": {"en": "Aggressive", "zh-TW": "積極"},
    "include_bsa": {
        "en": "Include BSA archives",
        "zh-TW": "包含 BSA 封存檔",
    },
    "scan_scope": {"en": "Scan scope", "zh-TW": "掃描範圍"},
    "fix": {"en": "Fix", "zh-TW": "修復"},
    "fix_needs_scan_hint": {
        "en": "Scan the target folder first.",
        "zh-TW": "請先掃描目標資料夾。",
    },
    "fix_needs_selection_hint": {
        "en": "Tick at least one row to fix.",
        "zh-TW": "請先勾選至少一個要修復的項目。",
    },
    "strength_hint": {
        "en": "Only applies to Heavy collision fixes.",
        "zh-TW": "僅套用於「過重碰撞網格」的修復。",
    },
    "options_changed_hint": {
        "en": "Options changed - scan again to refresh the results.",
        "zh-TW": "選項已變更，請重新掃描以更新結果。",
    },
    "status_ready": {"en": "Ready", "zh-TW": "就緒"},
    "status_scanning": {"en": "Scanning: {message}", "zh-TW": "掃描中：{message}"},
    "status_extracting": {"en": "Extracting: {message}", "zh-TW": "解壓縮中：{message}"},
    "status_fixing": {"en": "Fixing: {message}", "zh-TW": "修復中：{message}"},
    "status_fixing_progress": {
        "en": "Fixing {current}/{total}: {message}",
        "zh-TW": "修復中 {current}/{total}：{message}",
    },
    "status_scan_complete": {
        "en": "Scan complete: {count} item(s) found",
        "zh-TW": "掃描完成：找到 {count} 個項目",
    },
    "status_run_complete": {"en": "Fix run complete", "zh-TW": "修復作業完成"},
    "status_pause_requested": {
        "en": "Pause requested; finishing the current file safely...",
        "zh-TW": "已要求暫停；正在安全完成目前檔案……",
    },
    "status_paused": {
        "en": "Paused after {current}/{total} files",
        "zh-TW": "已暫停：完成 {current}/{total} 個檔案",
    },
    "status_resume_requested": {
        "en": "Resuming...",
        "zh-TW": "正在繼續……",
    },
    "status_resumed": {
        "en": "Resumed after {current}/{total} files",
        "zh-TW": "已繼續：完成 {current}/{total} 個檔案",
    },
    "status_stop_requested": {
        "en": "Stop requested; finishing the current file safely...",
        "zh-TW": "已要求中止；正在安全完成目前檔案……",
    },
    "status_run_stopped": {
        "en": "Stopped safely after {current}/{total} files",
        "zh-TW": "已安全中止：完成 {current}/{total} 個檔案",
    },
    "status_pending": {"en": "Pending", "zh-TW": "待處理"},
    "status_processing": {"en": "Processing", "zh-TW": "處理中"},
    "status_fixed": {"en": "Fixed", "zh-TW": "已修復"},
    "status_failed": {"en": "Failed", "zh-TW": "失敗"},
    "status_unfixable": {"en": "Unfixable", "zh-TW": "無法自動修復"},
    "status_skipped": {"en": "Skipped", "zh-TW": "已略過"},
    "status_error": {"en": "Error", "zh-TW": "錯誤"},
    "status_not_run": {"en": "Not run", "zh-TW": "未執行"},
    "pause": {"en": "Pause", "zh-TW": "暫停"},
    "resume": {"en": "Resume", "zh-TW": "繼續"},
    "stop": {"en": "Stop", "zh-TW": "中止"},
    "column_status": {"en": "Status", "zh-TW": "狀態"},
    "column_mesh": {"en": "Mesh", "zh-TW": "網格"},
    "column_selected": {"en": "Fix?", "zh-TW": "修復?"},
    "select_all": {"en": "Check all", "zh-TW": "全選"},
    "select_none": {"en": "Uncheck all", "zh-TW": "全不選"},
    "nothing_selected": {
        "en": "No rows are checked - tick the meshes you want to fix first.",
        "zh-TW": "沒有勾選任何項目——請先勾選要修復的網格。",
    },
    "column_verdict": {"en": "Before -> After", "zh-TW": "修復前 -> 修復後"},
    "column_categories": {"en": "Categories", "zh-TW": "類別"},
    "column_reason": {"en": "Reason", "zh-TW": "原因"},
    "count_summary": {
        "en": "Fixed {fixed}, failed {failed}, unfixable {unfixable}, skipped {skipped}, errors {error}, not run {not_run}",
        "zh-TW": "已修復 {fixed}、失敗 {failed}、無法修復 {unfixable}、略過 {skipped}、錯誤 {error}、未執行 {not_run}",
    },
    "pending_summary": {"en": "Pending {count}", "zh-TW": "待處理 {count}"},
    "failure_banner": {
        "en": (
            "Some files were not fixed - your game is NOT worse off: originals are "
            "untouched and failed files simply keep behaving as before. Options: "
            "re-run with a different strength (e.g. Aggressive), fix manually in "
            "Blender (see README 'Manual fallback'), or leave them - a heavy mesh "
            "only costs frames on contact, it does not crash. Details are in the "
            "report and in each row's tooltip."
        ),
        "zh-TW": (
            "部分檔案未修復——你的遊戲不會因此變糟：原始檔完全未動，失敗的檔案只是"
            "維持原本的行為。可行做法：換一個簡化強度重跑（例如「激進」）、依 README"
            "的「Manual fallback」用 Blender 手動修復，或者放著不管——過重碰撞只在"
            "接觸時掉幀，不會造成當機。詳情見報告與各列的滑鼠提示。"
        ),
    },
    "tooltip_failed": {
        "en": (
            "The fix was attempted but DeadMesh could not certify it safe, so nothing "
            "was written - the original file stays in effect.\n"
            "1. Try again with a different simplification strength (Aggressive).\n"
            "2. Fix manually in Blender + PyNifly (README: 'Manual fallback').\n"
            "3. Or leave it: a heavy mesh costs frames on contact, it does not crash."
        ),
        "zh-TW": (
            "已嘗試修復，但 DeadMesh 無法認證其安全，因此未輸出任何檔案——遊戲"
            "沿用原始檔。\n"
            "1. 換一個簡化強度（激進）再試一次。\n"
            "2. 用 Blender + PyNifly 手動修復（README：「Manual fallback」）。\n"
            "3. 或放著不管：過重碰撞只在接觸時掉幀，不會當機。"
        ),
    },
    "tooltip_unfixable": {
        "en": (
            "The collision geometry was stripped from this file (ORPHAN MOPP); there is "
            "nothing to rebuild from. Recreate the collision in Blender (README: "
            "'Manual fallback') or delete the dead block in NifSkope. Harmless in game."
        ),
        "zh-TW": (
            "此檔案的碰撞幾何已被刪除（ORPHAN MOPP），沒有可重建的來源。請用 Blender"
            "重建碰撞（README：「Manual fallback」）或在 NifSkope 中刪除殘留區塊。"
            "在遊戲中無害。"
        ),
    },
    "tooltip_error": {
        "en": (
            "An unexpected error, not a mesh verdict. Re-run once; if it persists, "
            "report it with deadmesh-fix-report.txt attached."
        ),
        "zh-TW": (
            "非網格判定的意外錯誤。請重跑一次；若持續發生，請附上 "
            "deadmesh-fix-report.txt 回報。"
        ),
    },
    "open_output": {"en": "Open output folder", "zh-TW": "開啟輸出資料夾"},
    "save_report": {"en": "Open report", "zh-TW": "開啟報告"},
    "language": {"en": "Language", "zh-TW": "語言"},
    "english": {"en": "English", "zh-TW": "English"},
    "traditional_chinese": {"en": "繁體中文", "zh-TW": "繁體中文"},
    "about": {"en": "About", "zh-TW": "關於"},
    "about_title": {"en": "About DeadMesh Fix Tool", "zh-TW": "關於 DeadMesh 修復工具"},
    "version": {"en": "Version", "zh-TW": "版本"},
    "about_text": {
        "en": "DeadMesh Fix Tool is GPL-3.0 software. Credits: TesFantom's DeadMesh and BadDogSkyrim's PyNifly. This is an unofficial companion and is not endorsed by either project.",
        "zh-TW": "DeadMesh 修復工具採用 GPL-3.0 授權。特別感謝 TesFantom 的 DeadMesh 與 BadDogSkyrim 的 PyNifly。本工具是非官方的搭配工具，並未獲得上述任一專案背書。",
    },
    "folder_required": {
        "en": "Select an existing target mod folder first.",
        "zh-TW": "請先選擇現有的目標模組資料夾。",
    },
    "output_required": {
        "en": "Select an output folder that is different from the target mod folder.",
        "zh-TW": "請選擇與目標模組資料夾不同的輸出資料夾。",
    },
    "scan_error_title": {"en": "Scan failed", "zh-TW": "掃描失敗"},
    "fix_error_title": {"en": "Fix run failed", "zh-TW": "修復作業失敗"},
    "worker_error": {
        "en": "Something went wrong. Details:\n{type}: {message}",
        "zh-TW": "發生非預期錯誤，請稍後重試或附上報告回報。詳細資訊：\n{type}：{message}",
    },
    "run_in_progress_title": {"en": "Run in progress", "zh-TW": "作業進行中"},
    "stop_and_close": {
        "en": "Stop safely after the current file and close the window?",
        "zh-TW": "是否在安全完成目前檔案後中止作業並關閉視窗？",
    },
    "run_in_progress": {
        "en": "A run is in progress and cannot be cancelled safely. Close the window automatically after it finishes?",
        "zh-TW": "作業正在進行，無法安全取消。是否在作業完成後自動關閉視窗？",
    },
    "no_report": {
        "en": "No text report is available yet.",
        "zh-TW": "目前尚無文字報告。",
    },
    "information": {"en": "Information", "zh-TW": "資訊"},
}

_language = "en"


def set_language(language: str) -> None:
    """Select the active language."""
    if language not in ("en", "zh-TW"):
        raise ValueError(f"unsupported language: {language}")
    global _language
    _language = language


def tr(key: str) -> str:
    """Return a translated UI string for the active language."""
    return STRINGS[key][_language]

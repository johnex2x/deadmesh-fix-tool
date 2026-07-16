"""PySide6 desktop interface for the DeadMesh fix pipeline."""
from __future__ import annotations

import shutil
import sys

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmfix import __version__
from dmfix.core.pipeline import (
    PipelineEvent,
    PipelineEventKind,
    PipelineOptions,
    RunControl,
    WorkItem,
    collect_work_items,
    default_mesh_output_dir,
    ensure_mesh_output_dir,
    report_output_dir,
    run_pipeline,
)
from dmfix.core.paths import icon_path
from dmfix.core.report import Outcome, RunReport
from dmfix.core.scanner import FixCategory, find_deadmesh_dir
from dmfix.gui.i18n import set_language, tr
from dmfix.gui.settings import Settings, load, save


CATEGORY_KEYS = {
    FixCategory.CRASH: "category_crash",
    FixCategory.HEAVY: "category_heavy",
    FixCategory.DEGENERATE: "category_degenerate",
    FixCategory.INVERTED: "category_inverted",
    FixCategory.ORPHAN_BLOCKS: "category_orphan_blocks",
    FixCategory.UNFIXABLE: "category_unfixable",
}
STRENGTHS = ("conservative", "normal", "aggressive")
OUTCOME_COLORS = {
    Outcome.FIXED: "#2e7d32",
    Outcome.FAILED: "#c62828",
    Outcome.UNFIXABLE: "#a35100",
    Outcome.SKIPPED: "#616161",
    Outcome.ERROR: "#616161",
    Outcome.NOT_RUN: "#616161",
    None: "#616161",
}


def derive_output_folder(target_folder: str) -> str:
    """Return the default loose-file output folder for a target mod."""
    if not target_folder.strip():
        return ""
    return str(default_mesh_output_dir(Path(target_folder)))


def output_folder_after_target_change(
    target_folder: str, current_output: str, manually_edited: bool
) -> str:
    """Derive a new default unless the user owns the output-folder value."""
    if manually_edited:
        return current_output
    return derive_output_folder(target_folder)


def is_safe_output_folder(target_folder: str, output_folder: str) -> bool:
    """Reject empty output paths and the one path that can overwrite originals."""
    if not output_folder.strip():
        return False
    target = Path(target_folder).resolve()
    output = Path(output_folder).resolve()
    return output != target and (not output.exists() or output.is_dir())


def is_valid_target_folder(target_folder: str) -> bool:
    """Require an explicit existing directory instead of treating blank as cwd."""
    return bool(target_folder.strip()) and Path(target_folder).is_dir()


class ScanWorker(QObject):
    progress = Signal(str, int, int, str)
    finished = Signal(object, object)
    error = Signal(str, str)

    def __init__(self, target_folder: Path, options: PipelineOptions) -> None:
        super().__init__()
        self.target_folder = target_folder
        self.options = options

    @Slot()
    def run(self) -> None:
        try:
            # collect_work_items removes its temp dir itself on failure; on
            # success the caller owns the returned dir and must clean it.
            items, temp_root = collect_work_items(
                self.target_folder, self.options, self.progress.emit
            )
        except Exception as error:
            self.error.emit(type(error).__name__, str(error))
            return
        self.finished.emit(items, temp_root)


class FixWorker(QObject):
    progress = Signal(str, int, int, str)
    event = Signal(object)
    finished = Signal(object)
    error = Signal(str, str)

    def __init__(
        self,
        target_folder: Path,
        options: PipelineOptions,
        work_items: list[WorkItem] | None = None,
    ) -> None:
        super().__init__()
        self.target_folder = target_folder
        self.options = options
        self.work_items = None if work_items is None else list(work_items)
        self.control = RunControl()

    @Slot()
    def run(self) -> None:
        try:
            report = run_pipeline(
                self.target_folder,
                self.options,
                self.progress.emit,
                control=self.control,
                on_event=self.event.emit,
                work_items=self.work_items,
            )
        except Exception as error:
            self.error.emit(type(error).__name__, str(error))
            return
        self.finished.emit(report)


class SetupDialog(QDialog):
    """First-run DeadMesh location prompt."""

    def __init__(self, suggestion: Path | None = None) -> None:
        super().__init__()
        self.setModal(True)

        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setOpenExternalLinks(True)
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        self.folder_label = QLabel()
        self.folder_edit = QLineEdit(str(suggestion or ""))
        self.browse_button = QPushButton()
        self.browse_button.clicked.connect(self._browse)
        self.continue_button = QPushButton()
        self.continue_button.clicked.connect(self._validate)
        self.quit_button = QPushButton()
        self.quit_button.clicked.connect(self.reject)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.continue_button)
        buttons.addWidget(self.quit_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.explanation)
        layout.addWidget(self.hint)
        layout.addWidget(self.folder_label)
        layout.addLayout(folder_row)
        layout.addLayout(buttons)
        self._retranslate()

    @property
    def deadmesh_dir(self) -> str:
        return self.folder_edit.text().strip()

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("setup_title"))
        self.explanation.setText(tr("setup_explanation"))
        self.hint.setText(tr("setup_hint"))
        self.folder_label.setText(tr("deadmesh_folder"))
        self.browse_button.setText(tr("browse"))
        self.continue_button.setText(tr("save_continue"))
        self.quit_button.setText(tr("quit"))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("select_deadmesh_folder"), self.deadmesh_dir
        )
        if folder:
            self.folder_edit.setText(folder)

    def _validate(self) -> None:
        if (Path(self.deadmesh_dir) / "dmscan.exe").is_file():
            self.accept()
            return
        QMessageBox.warning(self, tr("setup_title"), tr("invalid_deadmesh"))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        set_language(settings.language)
        self._output_manually_edited = False
        self._scan_temp_dir: Path | None = None
        self._pending_items: list[WorkItem] = []
        self._report: RunReport | None = None
        self._worker_thread: QThread | None = None
        self._worker: QObject | None = None
        self._running_kind = ""
        self._close_when_finished = False
        self._status_key = "status_ready"
        self._status_values: dict[str, object] = {}
        self._row_by_path: dict[str, int] = {}
        self._live_counts = {outcome.value: 0 for outcome in Outcome}

        self._build_ui()
        self._load_values()
        self._retranslate()
        self.resize(1050, 650)

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        # Colors chosen for >=4.5:1 contrast against white button text
        # (WCAG AA for normal text): #2f6fed ~4.55:1, #257a35 ~5.37:1.
        scan_button_style = (
            "QPushButton {"
            " background-color: #2f6fed; color: white; font-weight: 600;"
            " padding: 6px 20px; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a86f5; }"
            "QPushButton:pressed { background-color: #1f56c9; }"
            "QPushButton:disabled { background-color: #3a3a3a; color: #8a8a8a; }"
        )
        fix_button_style = (
            "QPushButton {"
            " background-color: #257a35; color: white; font-weight: 600;"
            " padding: 6px 20px; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #339c48; }"
            "QPushButton:pressed { background-color: #1c5c29; }"
            "QPushButton:disabled { background-color: #3a3a3a; color: #8a8a8a; }"
        )

        folders = QGridLayout()
        self.target_label = QLabel()
        self.target_edit = QLineEdit()
        self.target_browse = QPushButton()
        folders.addWidget(self.target_label, 0, 0)
        folders.addWidget(self.target_edit, 0, 1)
        folders.addWidget(self.target_browse, 0, 2)
        self.output_label = QLabel()
        self.output_edit = QLineEdit()
        self.output_browse = QPushButton()
        folders.addWidget(self.output_label, 1, 0)
        folders.addWidget(self.output_edit, 1, 1)
        folders.addWidget(self.output_browse, 1, 2)
        self.scan_scope_label = QLabel()
        self.include_bsa_check = QCheckBox()
        folders.addWidget(self.scan_scope_label, 2, 0)
        folders.addWidget(self.include_bsa_check, 2, 1)
        layout.addLayout(folders)

        self.scan_button = QPushButton()
        self.scan_button.setStyleSheet(scan_button_style)
        scan_row = QHBoxLayout()
        scan_row.addWidget(self.scan_button)
        scan_row.addStretch(1)
        layout.addLayout(scan_row)

        self.categories_group = QGroupBox()
        categories_layout = QGridLayout(self.categories_group)
        self.category_checks: dict[FixCategory, QCheckBox] = {}
        selectable = (
            FixCategory.CRASH,
            FixCategory.HEAVY,
            FixCategory.DEGENERATE,
            FixCategory.INVERTED,
            FixCategory.ORPHAN_BLOCKS,
        )
        for index, category in enumerate(selectable):
            checkbox = QCheckBox()
            self.category_checks[category] = checkbox
            categories_layout.addWidget(checkbox, index // 3, index % 3)
        self.strength_label = QLabel()
        self.strength_combo = QComboBox()
        for strength in STRENGTHS:
            self.strength_combo.addItem("", strength)
        categories_layout.addWidget(self.strength_label, 2, 0)
        categories_layout.addWidget(self.strength_combo, 2, 1)
        layout.addWidget(self.categories_group)

        action_row = QHBoxLayout()
        self.fix_button = QPushButton()
        self.fix_button.setEnabled(False)
        self.fix_button.setStyleSheet(fix_button_style)
        self.pause_button = QPushButton()
        self.pause_button.setEnabled(False)
        self.stop_button = QPushButton()
        self.stop_button.setEnabled(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label = QLabel()
        action_row.addWidget(self.fix_button)
        action_row.addWidget(self.pause_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(self.progress_bar, 1)
        action_row.addWidget(self.status_label, 2)
        layout.addLayout(action_row)

        self.results_table = QTableWidget(0, 6)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.results_table.itemChanged.connect(self._row_check_changed)
        self._populating_rows = False
        layout.addWidget(self.results_table, 1)
        selection_row = QHBoxLayout()
        self.count_label = QLabel()
        self.select_all_button = QPushButton()
        self.select_none_button = QPushButton()
        self.select_all_button.clicked.connect(lambda: self._set_all_checks(True))
        self.select_none_button.clicked.connect(lambda: self._set_all_checks(False))
        selection_row.addWidget(self.count_label, 1)
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.select_none_button)
        layout.addLayout(selection_row)
        self.failure_hint = QLabel()
        self.failure_hint.setWordWrap(True)
        self.failure_hint.setStyleSheet(
            "QLabel { background: rgba(198, 40, 40, 0.12); border: 1px solid #c62828;"
            " border-radius: 4px; padding: 6px; }"
        )
        self.failure_hint.hide()
        layout.addWidget(self.failure_hint)

        bottom = QHBoxLayout()
        self.open_output_button = QPushButton()
        self.report_button = QPushButton()
        self.report_button.setEnabled(False)
        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("", "en")
        self.language_combo.addItem("", "zh-TW")
        self.about_button = QPushButton()
        bottom.addWidget(self.open_output_button)
        bottom.addWidget(self.report_button)
        bottom.addStretch(1)
        bottom.addWidget(self.language_label)
        bottom.addWidget(self.language_combo)
        bottom.addWidget(self.about_button)
        layout.addLayout(bottom)

        self.target_browse.clicked.connect(self._browse_target)
        self.output_browse.clicked.connect(self._browse_output)
        self.scan_button.clicked.connect(self._scan)
        self.fix_button.clicked.connect(self._fix)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self._stop)
        self.target_edit.textChanged.connect(self._target_changed)
        self.output_edit.textEdited.connect(self._output_edited)
        for checkbox in self.category_checks.values():
            checkbox.toggled.connect(self._category_changed)
        self.strength_combo.currentIndexChanged.connect(self._save_controls)
        self.include_bsa_check.toggled.connect(self._scan_inputs_changed)
        self.open_output_button.clicked.connect(self._open_output)
        self.report_button.clicked.connect(self._open_report)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.about_button.clicked.connect(self._about)

    def _load_values(self) -> None:
        self.target_edit.setText(self.settings.last_target_folder)
        self.output_edit.setText(derive_output_folder(self.settings.last_target_folder))
        selected = set(self.settings.categories)
        for category, checkbox in self.category_checks.items():
            checkbox.setChecked(category.value in selected)
        strength_index = self.strength_combo.findData(self.settings.strength)
        self.strength_combo.setCurrentIndex(max(strength_index, 0))
        self.include_bsa_check.setChecked(self.settings.include_bsa)
        language_index = self.language_combo.findData(self.settings.language)
        self.language_combo.setCurrentIndex(max(language_index, 0))
        self._category_changed()

    def _retranslate(self) -> None:
        self.setWindowTitle(f"{tr('app_title')} {__version__}")
        self.target_label.setText(tr("target_folder"))
        self.output_label.setText(tr("output_folder"))
        self.target_browse.setText(tr("browse"))
        self.output_browse.setText(tr("browse"))
        self.scan_button.setText(tr("scan"))
        self.scan_scope_label.setText(tr("scan_scope"))
        self.include_bsa_check.setText(tr("include_bsa"))
        self.categories_group.setTitle(tr("fix_categories"))
        for category, checkbox in self.category_checks.items():
            checkbox.setText(tr(CATEGORY_KEYS[category]))
        self.strength_label.setText(tr("strength"))
        for index, strength in enumerate(STRENGTHS):
            self.strength_combo.setItemText(index, tr(f"strength_{strength}"))
        self.strength_combo.setToolTip(tr("strength_hint"))
        self.fix_button.setText(tr("fix"))
        self.pause_button.setText(tr("pause"))
        self.stop_button.setText(tr("stop"))
        self.results_table.setHorizontalHeaderLabels(
            [
                tr("column_selected"),
                tr("column_status"),
                tr("column_mesh"),
                tr("column_verdict"),
                tr("column_categories"),
                tr("column_reason"),
            ]
        )
        self.select_all_button.setText(tr("select_all"))
        self.select_none_button.setText(tr("select_none"))
        self.open_output_button.setText(tr("open_output"))
        self.report_button.setText(tr("save_report"))
        self.language_label.setText(tr("language"))
        self.language_combo.setItemText(0, tr("english"))
        self.language_combo.setItemText(1, tr("traditional_chinese"))
        self.about_button.setText(tr("about"))
        self._set_status(self._status_key, **self._status_values)
        self._render_rows()

    def _browse_target(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("select_target_folder"), self.target_edit.text()
        )
        if folder:
            self.target_edit.setText(folder)

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, tr("select_output_folder"), self.output_edit.text()
        )
        if folder:
            self._output_manually_edited = True
            self.output_edit.setText(str(ensure_mesh_output_dir(Path(folder))))

    def _target_changed(self, target: str) -> None:
        self.settings.last_target_folder = target.strip()
        self.output_edit.setText(
            output_folder_after_target_change(
                target, self.output_edit.text(), self._output_manually_edited
            )
        )
        self._invalidate_preview()

    def _output_edited(self) -> None:
        self._output_manually_edited = True

    def _selected_categories(self) -> set[FixCategory]:
        return {
            category
            for category, checkbox in self.category_checks.items()
            if checkbox.isChecked()
        }

    def _category_changed(self) -> None:
        self.strength_combo.setEnabled(
            self.category_checks[FixCategory.HEAVY].isChecked()
        )
        self._save_controls()
        self._update_fix_enabled()

    def _scan_inputs_changed(self) -> None:
        self._save_controls()
        self._invalidate_preview()

    def _invalidate_preview(self) -> None:
        had_results = bool(self._pending_items) or self._report is not None
        self._cleanup_scan_temp()
        self._pending_items = []
        self._report = None
        self.report_button.setEnabled(False)
        self._render_rows()
        if had_results:
            self._set_status("options_changed_hint")

    def _save_controls(self) -> None:
        if not hasattr(self, "category_checks"):
            return
        self.settings.categories = [
            category.value for category in self._selected_categories()
        ]
        self.settings.strength = str(self.strength_combo.currentData() or "normal")
        self.settings.include_bsa = self.include_bsa_check.isChecked()

    def _make_options(self) -> PipelineOptions:
        return PipelineOptions(
            deadmesh_dir=Path(self.settings.deadmesh_dir),
            output_dir=ensure_mesh_output_dir(
                Path(self.output_edit.text().strip())
            ),
            categories=self._selected_categories(),
            strength=str(self.strength_combo.currentData()),
            include_bsa=self.include_bsa_check.isChecked(),
        )

    def _valid_target(self) -> Path | None:
        target_text = self.target_edit.text().strip()
        if is_valid_target_folder(target_text):
            return Path(target_text)
        QMessageBox.warning(self, tr("app_title"), tr("folder_required"))
        return None

    def _valid_output(self, target: Path) -> bool:
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, tr("app_title"), tr("output_required"))
            return False
        output = ensure_mesh_output_dir(Path(output_text))
        self.output_edit.setText(str(output))
        if is_safe_output_folder(str(target), str(output)):
            return True
        QMessageBox.warning(self, tr("app_title"), tr("output_required"))
        return False

    def _scan(self) -> None:
        target = self._valid_target()
        if target is None:
            return
        self._cleanup_scan_temp()
        self._pending_items = []
        self._report = None
        self.report_button.setEnabled(False)
        self._render_rows()
        worker = ScanWorker(target, self._make_options())
        self._start_worker(worker, self._scan_finished, "scan")

    def _fix(self) -> None:
        target = self._valid_target()
        if target is None or not self._valid_output(target):
            return
        checked = self._checked_paths()
        if not checked:
            QMessageBox.information(self, tr("app_title"), tr("nothing_selected"))
            return
        self._cleanup_scan_temp()
        options = self._make_options()
        options.only_paths = checked
        # A first fix run owns cleanup; any subsequent selected retry must
        # preserve files already certified in the Meshes root.
        options.clean_previous_outputs = self._report is None
        worker = FixWorker(target, options, self._pending_items)
        self._live_counts = {outcome.value: 0 for outcome in Outcome}
        self._start_worker(worker, self._fix_finished, "fix")

    def _toggle_pause(self) -> None:
        worker = self._worker
        if not isinstance(worker, FixWorker):
            return
        if worker.control.is_paused:
            worker.control.resume()
            self.pause_button.setText(tr("pause"))
            self._set_status("status_resume_requested")
        else:
            worker.control.request_pause()
            self.pause_button.setText(tr("resume"))
            self._set_status("status_pause_requested")

    def _stop(self) -> None:
        worker = self._worker
        if not isinstance(worker, FixWorker):
            return
        worker.control.request_stop()
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_status("status_stop_requested")

    def _start_worker(
        self,
        worker: ScanWorker | FixWorker,
        success: Callable[..., None],
        kind: str,
    ) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.progress.connect(self._progress)
        if isinstance(worker, FixWorker):
            worker.event.connect(self._pipeline_event)
        worker.finished.connect(success)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        worker.error.connect(self._worker_error)
        worker.error.connect(worker.deleteLater)
        worker.error.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._worker = worker
        self._worker_thread = thread
        self._running_kind = kind
        self.scan_button.setEnabled(False)
        self.fix_button.setEnabled(False)
        self.pause_button.setEnabled(kind == "fix")
        self.stop_button.setEnabled(kind == "fix")
        self._set_inputs_enabled(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._set_status("status_scanning" if kind == "scan" else "status_fixing", message="")
        thread.start()

    @Slot(object)
    def _pipeline_event(self, event: object) -> None:
        if not isinstance(event, PipelineEvent):
            return
        self.progress_bar.setRange(0, max(event.total, 1))
        row = self._row_by_path.get(event.relative_path.replace("/", "\\").lower())
        if event.kind is PipelineEventKind.ITEM_STARTED:
            self.progress_bar.setValue(event.current)
            if row is not None:
                self.results_table.item(row, 1).setText(tr("status_processing"))
            self._set_status(
                "status_fixing_progress",
                current=event.current + 1,
                total=event.total,
                message=event.relative_path,
            )
        elif event.kind is PipelineEventKind.ITEM_PROGRESS:
            if (
                isinstance(self._worker, FixWorker)
                and self._worker.control.is_stop_requested
            ):
                return
            self._set_status(
                "status_fixing_progress",
                current=event.current + 1,
                total=event.total,
                message=event.message,
            )
        elif event.kind is PipelineEventKind.ITEM_COMPLETED and event.result is not None:
            self.progress_bar.setValue(event.current)
            if row is not None:
                self._update_result_row(row, event.result)
            self._live_counts[event.result.outcome.value] += 1
            self.count_label.setText(
                tr("count_summary").format(**self._live_counts)
            )
        elif event.kind is PipelineEventKind.RUN_PAUSED:
            self._set_status(
                "status_paused", current=event.current, total=event.total
            )
        elif event.kind is PipelineEventKind.RUN_RESUMED:
            self.pause_button.setText(tr("pause"))
            self._set_status(
                "status_resumed", current=event.current, total=event.total
            )
        elif event.kind is PipelineEventKind.RUN_STOPPED:
            self._set_status(
                "status_run_stopped", current=event.current, total=event.total
            )

    @Slot(str, int, int, str)
    def _progress(self, stage: str, current: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(min(current, max(total, 1)))
        key = {
            "scan": "status_scanning",
            "extract": "status_extracting",
            "fix": "status_fixing",
        }.get(stage, "status_scanning")
        self._set_status(key, message=message)

    @Slot(object, object)
    def _scan_finished(self, items: object, temp_root: object) -> None:
        self._pending_items = list(items)  # type: ignore[arg-type]
        self._scan_temp_dir = Path(temp_root)  # type: ignore[arg-type]
        self.progress_bar.setValue(self.progress_bar.maximum())
        self._set_status("status_scan_complete", count=len(self._pending_items))
        self._render_rows()

    @Slot(object)
    def _fix_finished(self, report: object) -> None:
        self._report = report if isinstance(report, RunReport) else None
        self.progress_bar.setValue(self.progress_bar.maximum())
        if self._report is not None and self._report.status == "stopped":
            self.progress_bar.setValue(self._report.processed_items)
            self._set_status(
                "status_run_stopped",
                current=self._report.processed_items,
                total=self._report.total_items,
            )
        else:
            self._set_status("status_run_complete")
        self.report_button.setEnabled(self._report is not None)
        self._render_rows()

    @Slot(str, str)
    def _worker_error(self, error_type: str, message: str) -> None:
        title_key = "scan_error_title" if self._running_kind == "scan" else "fix_error_title"
        if not self._close_when_finished:
            detail = tr("worker_error").format(type=error_type, message=message)
            QMessageBox.critical(self, tr(title_key), detail)
        self._set_status("status_ready")

    @Slot()
    def _thread_finished(self) -> None:
        self.pause_button.setEnabled(False)
        self.pause_button.setText(tr("pause"))
        self.stop_button.setEnabled(False)
        self._worker = None
        self._worker_thread = None
        self._running_kind = ""
        self._set_inputs_enabled(True)
        self.scan_button.setEnabled(True)
        self._update_fix_enabled()
        if self._close_when_finished:
            QTimer.singleShot(0, self.close)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.target_edit,
            self.target_browse,
            self.output_edit,
            self.output_browse,
            self.categories_group,
            self.language_combo,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self.strength_combo.setEnabled(
                self.category_checks[FixCategory.HEAVY].isChecked()
            )

    def _update_fix_enabled(self) -> None:
        checked = bool(self._checked_paths())
        self.fix_button.setEnabled(self._worker_thread is None and checked)
        if checked:
            self.fix_button.setToolTip("")
        elif not self._pending_items and self._report is None:
            self.fix_button.setToolTip(tr("fix_needs_scan_hint"))
        else:
            self.fix_button.setToolTip(tr("fix_needs_selection_hint"))

    def _set_status(self, key: str, **values: object) -> None:
        self._status_key = key
        self._status_values = values
        self.status_label.setText(tr(key).format(**values))

    def _category_text(self, categories: list[FixCategory] | list[str]) -> str:
        labels = []
        for category in categories:
            try:
                enum_value = category if isinstance(category, FixCategory) else FixCategory(category)
            except ValueError:
                continue
            labels.append(tr(CATEGORY_KEYS[enum_value]))
        return ", ".join(labels)

    def _render_rows(self) -> None:
        self._populating_rows = True
        try:
            self.results_table.setRowCount(0)
            self._row_by_path.clear()
            if self._report is not None:
                for result in self._report.results:
                    # After a run, only rows the user may want to retry are
                    # checkable, and they start checked so "Fix" immediately
                    # re-runs just the failures (e.g. with another strength).
                    retryable = result.outcome in (
                        Outcome.FAILED,
                        Outcome.ERROR,
                        Outcome.NOT_RUN,
                    )
                    self._add_result_row(
                        tr(f"status_{result.outcome.value}"),
                        result.relative_path,
                        f"{result.verdict_before} -> {result.verdict_after or '-'}",
                        self._category_text(result.categories),
                        result.reason,
                        result.outcome,
                        checkable=retryable,
                        checked=retryable,
                    )
                    self._row_by_path[result.relative_path.replace("/", "\\").lower()] = (
                        self.results_table.rowCount() - 1
                    )
                counts = self._report.counts()
                self.count_label.setText(tr("count_summary").format(**counts))
                if counts["failed"] or counts["unfixable"] or counts["error"]:
                    self.failure_hint.setText(tr("failure_banner"))
                    self.failure_hint.show()
                else:
                    self.failure_hint.hide()
            else:
                for item in self._pending_items:
                    record = item.record
                    if record is None:
                        continue
                    self._add_result_row(
                        tr("status_pending"),
                        item.relative_path,
                        f"{record.verdict} -> -",
                        self._category_text(record.categories),
                        "",
                        None,
                        checkable=True,
                        checked=True,
                    )
                    self._row_by_path[item.relative_path.replace("/", "\\").lower()] = (
                        self.results_table.rowCount() - 1
                    )
                self.count_label.setText(
                    tr("pending_summary").format(count=len(self._pending_items))
                )
                self.failure_hint.hide()
        finally:
            self._populating_rows = False
        self._update_fix_enabled()

    def _checked_paths(self) -> set[str]:
        checked: set[str] = set()
        for row in range(self.results_table.rowCount()):
            item = self.results_table.item(row, 0)
            if (
                item is not None
                and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                and item.checkState() == Qt.CheckState.Checked
            ):
                checked.add(self.results_table.item(row, 2).text())
        return checked

    def _set_all_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._populating_rows = True
        try:
            for row in range(self.results_table.rowCount()):
                item = self.results_table.item(row, 0)
                if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    item.setCheckState(state)
        finally:
            self._populating_rows = False
        self._update_fix_enabled()

    @Slot(QTableWidgetItem)
    def _row_check_changed(self, item: QTableWidgetItem) -> None:
        if not self._populating_rows and item.column() == 0:
            self._update_fix_enabled()

    def _add_result_row(
        self,
        status: str,
        mesh: str,
        verdict: str,
        categories: str,
        reason: str,
        outcome: Outcome | None,
        *,
        checkable: bool = False,
        checked: bool = False,
    ) -> None:
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        check_item = QTableWidgetItem()
        if checkable:
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
        else:
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.results_table.setItem(row, 0, check_item)
        values = (status, mesh, verdict, categories, reason)
        tooltip_key = {
            Outcome.FAILED: "tooltip_failed",
            Outcome.UNFIXABLE: "tooltip_unfixable",
            Outcome.ERROR: "tooltip_error",
        }.get(outcome)
        for offset, value in enumerate(values):
            item = QTableWidgetItem(value)
            if tooltip_key:
                item.setToolTip(tr(tooltip_key))
            self.results_table.setItem(row, offset + 1, item)
        # Colors are chosen for >=4.5:1 contrast against a white table
        # background (WCAG AA for normal text); the stock Material orange
        # and grey (#ef6c00 / #757575) fall short of that (~3.1:1 / ~4.6:1).
        self.results_table.item(row, 1).setForeground(
            QColor(OUTCOME_COLORS[outcome])
        )

    def _update_result_row(self, row: int, result: FileResult) -> None:
        values = (
            tr(f"status_{result.outcome.value}"),
            result.relative_path,
            f"{result.verdict_before} -> {result.verdict_after or '-'}",
            self._category_text(result.categories),
            result.reason,
        )
        for offset, value in enumerate(values, start=1):
            self.results_table.item(row, offset).setText(value)
        self.results_table.item(row, 1).setForeground(
            QColor(OUTCOME_COLORS[result.outcome])
        )

    def _open_output(self) -> None:
        folder = self.output_edit.text().strip()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _open_report(self) -> None:
        report_path = (
            report_output_dir(Path(self.output_edit.text().strip()))
            / "deadmesh-fix-report.txt"
        )
        if report_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report_path)))
            return
        QMessageBox.information(self, tr("information"), tr("no_report"))

    def _language_changed(self) -> None:
        language = str(self.language_combo.currentData() or "en")
        set_language(language)
        self.settings.language = language
        self._retranslate()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"{tr('about_title')} {__version__}",
            f"{tr('version')}: {__version__}\n\n{tr('about_text')}",
        )

    def _cleanup_scan_temp(self) -> None:
        if self._scan_temp_dir is not None:
            shutil.rmtree(self._scan_temp_dir, ignore_errors=True)
            self._scan_temp_dir = None

    def _persist_settings(self) -> None:
        self._save_controls()
        self.settings.last_target_folder = self.target_edit.text().strip()
        save(self.settings)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker_thread is not None:
            can_stop = isinstance(self._worker, FixWorker)
            answer = QMessageBox.question(
                self,
                tr("run_in_progress_title"),
                tr("stop_and_close" if can_stop else "run_in_progress"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is QMessageBox.StandardButton.Yes:
                if can_stop:
                    self._stop()
                self._close_when_finished = True
                self.hide()
            event.ignore()
            return
        self._cleanup_scan_temp()
        self._persist_settings()
        event.accept()


def run_gui() -> int:
    """Create and run the Qt application."""
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)
    icon = QIcon(str(icon_path()))
    app.setWindowIcon(icon)

    settings = load()
    if settings.language not in ("en", "zh-TW"):
        settings.language = "en"
    set_language(settings.language)
    if not (Path(settings.deadmesh_dir) / "dmscan.exe").is_file():
        suggestion = find_deadmesh_dir()
        dialog = SetupDialog(suggestion)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
        settings.deadmesh_dir = dialog.deadmesh_dir
        save(settings)

    window = MainWindow(settings)
    window.show()
    if owns_app:
        return app.exec()
    return 0

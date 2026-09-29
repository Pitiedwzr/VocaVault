"""PySide6 user interface for the VocaVault local library."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import (
    QMimeData,
    QObject,
    QPoint,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QCloseEvent, QDrag, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .ui_widgets import MultilineEdit, PathLabel, WrappingLabel

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".svp", ".ust", ".vsqx", ".ustx", ".vpr", ".ccs", ".ppsf", ".mid"}
)


def _value(source: Any, *names: str, default: Any = None) -> Any:
    """Read the first available field from mappings, rows, or objects."""
    if source is None:
        return default
    for name in names:
        if isinstance(source, Mapping) and name in source:
            value = source[name]
        else:
            try:
                value = getattr(source, name)
            except (AttributeError, TypeError):
                continue
        if value is not None:
            return value
    return default


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_text(item) for item in value if item is not None)
    return str(value)


def _display_name(row: Any) -> str:
    return _text(
        _value(row, "name", "project_name", "display_name", "title"), "Untitled"
    )


def _files(row: Any) -> list[Any]:
    files = _value(row, "files", "file_records", default=None)
    if isinstance(files, Sequence) and not isinstance(files, (str, bytes, bytearray)):
        return list(files)
    if _value(row, "file_id", "default_file_id", default=None) is not None:
        return [row]
    nested = _value(row, "default_file", "file", default=None)
    return [nested] if nested is not None else []


def _file_id(file_record: Any) -> Any:
    return _value(file_record, "id", "file_id", "default_file_id", default=None)


class _WorkerSignals(QObject):
    result = Signal(int, object, object)
    error = Signal(int, str)
    finished = Signal(int)


class _JobContext:
    """Thread-safe cancellation and progress channel for one worker job."""

    def __init__(
        self,
        job_id: int,
        cancelled: Event,
        report_progress: Callable[[int, int, str], None],
    ) -> None:
        self.job_id = job_id
        self.cancelled = cancelled
        self.report_progress = report_progress


class _Worker(QRunnable):
    """Run one service operation outside the GUI thread."""

    def __init__(
        self,
        context: _JobContext,
        function: Callable[[_JobContext], Any],
        on_result: Callable[[Any], None],
    ) -> None:
        super().__init__()
        self.context = context
        self.function = function
        self.on_result = on_result
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        if self.context.cancelled.is_set():
            self.signals.finished.emit(self.context.job_id)
            return
        try:
            result = self.function(self.context)
        except Exception as exc:  # noqa: BLE001 - surface worker errors in the UI.
            self.signals.error.emit(
                self.context.job_id, str(exc) or exc.__class__.__name__
            )
        else:
            self.signals.result.emit(self.context.job_id, self.on_result, result)
        finally:
            self.signals.finished.emit(self.context.job_id)


def create_file_drag(source: QWidget, file_path: str | Path) -> QDrag:
    """Construct an outbound QDrag using copy semantics for an external editor."""
    path_str = str(Path(file_path).resolve())
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(path_str)])
    mime_data.setText(path_str)
    drag = QDrag(source)
    drag.setMimeData(mime_data)
    return drag


class DraggableFileLabel(WrappingLabel):
    """Draggable label allowing users to drop project files into external editors."""

    def __init__(
        self, get_file_path: Callable[[], str | None], parent: QWidget | None = None
    ) -> None:
        super().__init__("Drag files into supported applications", parent)
        self.get_file_path = get_file_path
        self._drag_start_pos: QPoint | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag files into supported applications")
        self.setStyleSheet(
            "padding: 6px; border: 1px dashed palette(mid); border-radius: 4px;"
        )

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            distance = (
                event.position().toPoint() - self._drag_start_pos
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                path = self.get_file_path()
                if path and Path(path).exists():
                    self._drag_start_pos = None
                    drag = create_file_drag(self, path)
                    drag.exec(Qt.DropAction.CopyAction)
                    return
        super().mouseMoveEvent(event)


class ProjectTableWidget(QTableWidget):
    """Project table supporting row selection and outbound drag to external editors."""

    def __init__(
        self,
        rows: int,
        columns: int,
        get_project_file: Callable[[int], str | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(rows, columns, parent)
        self.get_project_file = get_project_file
        self._drag_start_pos: QPoint | None = None

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            distance = (
                event.position().toPoint() - self._drag_start_pos
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                item = self.itemAt(self._drag_start_pos)
                if item:
                    row = item.row()
                    path = self.get_project_file(row)
                    if path and Path(path).exists():
                        self._drag_start_pos = None
                        drag = create_file_drag(self, path)
                        drag.exec(Qt.DropAction.CopyAction)
                        return
        super().mouseMoveEvent(event)


class CreateVersionDialog(QDialog):
    """Dialog for creating a new named version in a project."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Version")
        self.resize(360, 200)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.label_edit = QLineEdit(self)
        self.label_edit.setPlaceholderText("e.g. Revision 2, Harmony Mix")
        self.notes_edit = QLineEdit(self)
        self.notes_edit.setPlaceholderText("Optional version notes")
        self.terms_edit = QLineEdit(self)
        self.terms_edit.setPlaceholderText("e.g. CC-BY, Non-commercial only")

        form.addRow("Version Label*", self.label_edit)
        form.addRow("Notes", self.notes_edit)
        form.addRow("Distribution Terms", self.terms_edit)
        layout.addLayout(form)

        btn_box = QHBoxLayout()
        self.ok_button = QPushButton("Create", self)
        self.ok_button.clicked.connect(self._validate_and_accept)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        btn_box.addStretch(1)
        btn_box.addWidget(self.cancel_button)
        btn_box.addWidget(self.ok_button)
        layout.addLayout(btn_box)

    def _validate_and_accept(self) -> None:
        if not self.label_edit.text().strip():
            QMessageBox.warning(self, "Invalid Input", "Version label cannot be empty.")
            return
        self.accept()


class GroupProjectsDialog(QDialog):
    """Dialog for merging the current project into another target project."""

    def __init__(
        self,
        current_project_id: str,
        current_project_name: str,
        other_projects: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.current_project_id = current_project_id
        self.other_projects = other_projects

        self.setWindowTitle("Group into Project")
        self.resize(420, 220)

        layout = QVBoxLayout(self)
        info = QLabel(
            f"Select the target project to merge <b>{current_project_name}</b> into.<br>"
            "All versions, files, credits, and tags will be preserved.",
            self,
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.project_combo = QComboBox(self)
        for p in other_projects:
            p_name = _text(_value(p, "name", "project_name"))
            file_count = _value(p, "file_count", default=len(_files(p)))
            self.project_combo.addItem(f"{p_name} ({file_count} file(s))", p["id"])
        layout.addWidget(self.project_combo)

        layout.addStretch(1)
        btn_box = QHBoxLayout()
        self.ok_button = QPushButton("Group", self)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        btn_box.addStretch(1)
        btn_box.addWidget(self.cancel_button)
        btn_box.addWidget(self.ok_button)
        layout.addLayout(btn_box)

    @property
    def selected_target_id(self) -> str:
        return self.project_combo.currentData()


class MoveFileToVersionDialog(QDialog):
    """Dialog for moving a file to a different version in the project."""

    def __init__(
        self,
        versions: list[dict[str, Any]],
        current_version_id: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Move File to Version")
        self.resize(360, 160)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select target version:", self))

        self.version_combo = QComboBox(self)
        for v in versions:
            v_id = _value(v, "id")
            v_label = _value(v, "label")
            if v_id != current_version_id:
                self.version_combo.addItem(v_label, v_id)
        layout.addWidget(self.version_combo)

        layout.addStretch(1)
        btn_box = QHBoxLayout()
        self.ok_button = QPushButton("Move", self)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        btn_box.addStretch(1)
        btn_box.addWidget(self.cancel_button)
        btn_box.addWidget(self.ok_button)
        layout.addLayout(btn_box)

    @property
    def selected_version_id(self) -> str:
        return self.version_combo.currentData()


class VocaDbDialog(QDialog):
    """Dialog for searching VocaDB candidates, reviewing proposed fields, and confirming enrichment."""

    def __init__(
        self,
        library: Any,
        project: dict[str, Any],
        parent: QWidget | None = None,
        *,
        refresh: bool = False,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.thread_pool = QThreadPool.globalInstance()
        self._generation = 0
        self._closed = False
        self._applying = False
        self._workers = {}
        self._refresh = refresh
        self.project = project
        self.selected_candidate: dict[str, Any] | None = None
        self._candidates: list[dict[str, Any]] = []

        self.setWindowTitle("VocaDB Song Enrichment")
        self.resize(700, 680)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        initial_query = _text(_value(project, "name", "project_name"))
        self.search_edit.setText(initial_query)
        self.search_button = QPushButton("Search VocaDB", self)
        self.search_button.clicked.connect(self._do_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.status_label = QLabel(self)
        self.status_label.setStyleSheet("color: palette(text); font-style: italic;")
        layout.addWidget(self.status_label)

        self.candidates_table = QTableWidget(0, 4, self)
        self.candidates_table.setHorizontalHeaderLabels(
            ["ID", "Title", "Artist", "Type"]
        )
        self.candidates_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.candidates_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.candidates_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.candidates_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.candidates_table.itemSelectionChanged.connect(self._on_candidate_selected)
        layout.addWidget(self.candidates_table, 1)

        preview_group = QFrame(self)
        preview_group.setFrameShape(QFrame.Shape.StyledPanel)
        preview_layout = QVBoxLayout(preview_group)

        self.name_check = QCheckBox("Apply Display Name", preview_group)
        self.name_check.setChecked(not refresh)
        self.aliases_check = QCheckBox("Apply Song Aliases", preview_group)
        self.aliases_check.setChecked(True)
        self.credits_check = QCheckBox("Apply Original Song Credits", preview_group)
        self.credits_check.setChecked(True)
        self.links_check = QCheckBox("Apply Media Links", preview_group)
        self.links_check.setChecked(True)

        chk_row = QVBoxLayout()
        chk_row.addWidget(self.name_check)
        chk_row.addWidget(self.aliases_check)
        chk_row.addWidget(self.credits_check)
        chk_row.addWidget(self.links_check)
        preview_layout.addLayout(chk_row)

        self.preview_text = WrappingLabel(
            "Select a candidate to preview fields.", preview_group
        )
        self.preview_text.setWordWrap(True)
        preview_layout.addWidget(self.preview_text)

        terms_note = QLabel(
            "Note: Song-level enrichment preserves tuner credits and version distribution terms.",
            preview_group,
        )
        terms_note.setStyleSheet("color: palette(text); font-size: 11px;")
        preview_layout.addWidget(terms_note)
        preview_scroll = QScrollArea(self)
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        preview_scroll.setWidget(preview_group)
        layout.addWidget(preview_scroll, 1)

        btn_box = QHBoxLayout()
        self.apply_button = QPushButton("Apply Enrichment", self)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_enrichment)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.reject)
        btn_box.addStretch(1)
        btn_box.addWidget(self.cancel_button)
        btn_box.addWidget(self.apply_button)
        layout.addLayout(btn_box)

        if refresh:
            self.status_label.setText("Fetching current VocaDB fields for review…")
            self._start_task(
                lambda: [
                    self.library.preview_vocadb_refresh(_text(self.project["id"]))
                ],
                self._search_complete,
            )
        elif initial_query:
            self._do_search()

    def _do_search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        self.status_label.setText("Searching VocaDB…")
        self.candidates_table.setRowCount(0)
        self.selected_candidate = None
        self.apply_button.setEnabled(False)
        self.preview_text.setText("Searching…")

        self._start_task(
            lambda: self.library.search_vocadb_candidates(query), self._search_complete
        )

    def _search_complete(self, candidates):
        self._candidates = [
            cand
            if isinstance(cand, dict)
            else {
                "id": getattr(cand, "id", 0),
                "name": getattr(cand, "name", ""),
                "artist_string": getattr(cand, "artist_string", ""),
                "song_type": getattr(cand, "song_type", ""),
                "names": getattr(cand, "names", ()),
                "artists": getattr(cand, "artists", ()),
                "links": getattr(cand, "links", ()),
            }
            for cand in candidates
        ]
        self.candidates_table.setRowCount(len(self._candidates))
        for row, cand in enumerate(self._candidates):
            self.candidates_table.setItem(row, 0, QTableWidgetItem(str(cand.get("id"))))
            self.candidates_table.setItem(
                row, 1, QTableWidgetItem(str(cand.get("name")))
            )
            self.candidates_table.setItem(
                row, 2, QTableWidgetItem(str(cand.get("artist_string")))
            )
            self.candidates_table.setItem(
                row, 3, QTableWidgetItem(str(cand.get("song_type")))
            )
        if self._candidates:
            self.status_label.setText(f"Found {len(self._candidates)} candidate(s).")
            self.candidates_table.selectRow(0)
        else:
            self.status_label.setText("No candidates found on VocaDB.")
            self.preview_text.setText("No matches found.")

    def _start_task(self, function, callback):
        self._generation += 1
        generation = self._generation
        context = _JobContext(generation, Event(), lambda *_: None)
        worker = _Worker(context, lambda _: function(), callback)
        worker.signals.result.connect(
            self._task_result, Qt.ConnectionType.QueuedConnection
        )
        worker.signals.error.connect(
            self._task_error, Qt.ConnectionType.QueuedConnection
        )
        worker.signals.finished.connect(
            self._task_finished, Qt.ConnectionType.QueuedConnection
        )
        self._workers[generation] = worker
        self.thread_pool.start(worker)

    @Slot(int, object, object)
    def _task_result(self, generation, callback, value):
        if not self._closed and generation == self._generation:
            callback(value)

    @Slot(int, str)
    def _task_error(self, generation, message):
        if not self._closed and generation == self._generation:
            self._applying = False
            self.search_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.status_label.setText(
                "Could not reach VocaDB or finish the operation. Local work remains available."
            )
            self.preview_text.setText(message)

    @Slot(int)
    def _task_finished(self, generation):
        self._workers.pop(generation, None)

    def reject(self):
        if self._applying:
            return
        self._closed = True
        self._generation += 1
        super().reject()

    def _on_candidate_selected(self) -> None:
        selected_rows = self.candidates_table.selectionModel().selectedRows()
        if not selected_rows:
            self.selected_candidate = None
            self.apply_button.setEnabled(False)
            self.preview_text.setText("Select a candidate to preview fields.")
            return
        row = selected_rows[0].row()
        cand = self._candidates[row]
        self.selected_candidate = cand
        self.apply_button.setEnabled(True)

        preview_lines = [
            f"Current project: {_display_name(self.project)}",
            f"Proposed title: {_text(cand.get('name'))}",
            f"Artist(s): {_text(cand.get('artist_string'))}",
        ]
        names = cand.get("names", [])
        if names:
            alias_strs = [
                f"{n.get('value')} ({n.get('language')})"
                for n in names
                if isinstance(n, dict)
            ]
            preview_lines.append(f"Aliases: {', '.join(alias_strs)}")
        artists = cand.get("artists", [])
        if artists:
            art_strs = [
                f"{a.get('name')} ({a.get('roles') or a.get('categories')})"
                for a in artists
                if isinstance(a, dict)
            ]
            preview_lines.append(f"Original song credits: {', '.join(art_strs)}")
        links = cand.get("links", [])
        if links:
            link_strs = [
                f"{l.get('label')}: {l.get('url')}"
                for l in links
                if isinstance(l, dict)
            ]
            preview_lines.append(f"Links: {', '.join(link_strs[:3])}")

        self.preview_text.setText("\n\n".join(preview_lines))

    def _apply_enrichment(self) -> None:
        if not self.selected_candidate:
            return
        project_id = _text(_value(self.project, "id", "project_id"))
        candidate = dict(self.selected_candidate)
        options = {
            "apply_name": self.name_check.isChecked(),
            "apply_aliases": self.aliases_check.isChecked(),
            "apply_credits": self.credits_check.isChecked(),
            "apply_links": self.links_check.isChecked(),
            "overwrite_overrides": self.name_check.isChecked(),
        }
        self._applying = True
        self.apply_button.setEnabled(False)
        self.search_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Applying selected fields…")
        self._start_task(
            lambda: self.library.apply_vocadb_enrichment(
                project_id, candidate, **options
            ),
            self._applied,
        )

    def _applied(self, _result):
        self._applying = False
        self._closed = True
        self.accept()


class MainWindow(QMainWindow):
    """Three-panel desktop shell backed by a ``LibraryService`` facade."""

    TABLE_COLUMNS = ("Project", "Status", "Engine", "Language", "Files", "Health")
    _worker_progress = Signal(int, int, int, str)

    def __init__(self, library: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.thread_pool = QThreadPool(self)
        self._projects: dict[str, Any] = {}
        self._selected_project: Any = None
        self._loading_inspector = True
        self._project_snapshot = None
        self._version_snapshot = None
        self._editing_version = None
        self._selection_generation = 0
        self._restore_version_id = None
        self.settings = QSettings("VocaVault", "VocaVault")
        self._search_generation = 0
        self._active_jobs = 0
        self._next_job_id = 1
        self._jobs: dict[int, _JobContext] = {}
        self._mutation_jobs: set[int] = set()
        self._cancellable_jobs: set[int] = set()
        self._closing_when_idle = False

        self.setWindowTitle("VocaVault")
        self.resize(1280, 760)
        self.setMinimumSize(900, 560)
        self.setAcceptDrops(True)

        self._create_actions()
        self._create_toolbar()
        self._create_panels()
        self._create_status_bar()
        self._worker_progress.connect(
            self._show_worker_progress, Qt.ConnectionType.QueuedConnection
        )

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.reload_projects)

        self.search_edit.textChanged.connect(self._schedule_search)
        self.engine_filter.currentIndexChanged.connect(self.reload_projects)
        self.language_filter.currentIndexChanged.connect(self.reload_projects)
        self.voice_filter.textChanged.connect(self._schedule_search)
        self.tuning_filter.currentIndexChanged.connect(self.reload_projects)
        self.version_scope.currentIndexChanged.connect(self.reload_projects)
        self.health_filter.currentIndexChanged.connect(self.reload_projects)
        self.project_table.itemSelectionChanged.connect(self._show_selected_project)
        self.file_combo.currentIndexChanged.connect(self._update_file_details)

        self._loading_inspector = False
        self._restore_layout()
        self.reload_projects()

    def _create_actions(self) -> None:
        self.import_files_action = QAction("Import Files…", self)
        self.import_files_action.setShortcut(QKeySequence.StandardKey.Open)
        self.import_files_action.triggered.connect(self.import_files)

        self.import_folder_action = QAction("Import Folder…", self)
        self.import_folder_action.triggered.connect(self.import_folder)

        self.refresh_action = QAction("Refresh File", self)
        self.refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        self.refresh_action.triggered.connect(self.refresh_selected_file)

        self.relink_action = QAction("Relink File…", self)
        self.relink_action.triggered.connect(self.relink_selected_file)

        self.open_action = QAction("Open", self)
        self.open_action.triggered.connect(self.open_selected_file)

        self.open_with_action = QAction("Open with…", self)
        self.open_with_action.triggered.connect(self.open_selected_file_with)

        self.reveal_action = QAction("Reveal", self)
        self.reveal_action.triggered.connect(self.reveal_selected_file)

        self.backup_action = QAction("Back Up Metadata…", self)
        self.backup_action.triggered.connect(self.backup_library)

        self.restore_action = QAction("Restore Metadata…", self)
        self.restore_action.triggered.connect(self.restore_library)

        self.export_action = QAction("Export Metadata…", self)
        self.export_action.triggered.connect(self.export_library)

        self.group_project_action = QAction("Group with Project…", self)
        self.group_project_action.triggered.connect(self.group_selected_project)

        self.enrich_vocadb_action = QAction("Enrich with VocaDB…", self)
        self.enrich_vocadb_action.triggered.connect(self.enrich_selected_with_vocadb)

        self.refresh_vocadb_action = QAction("Refresh from VocaDB", self)
        self.refresh_vocadb_action.triggered.connect(self.refresh_selected_vocadb)

        self.unlink_vocadb_action = QAction("Unlink VocaDB", self)
        self.unlink_vocadb_action.triggered.connect(self.unlink_selected_vocadb)

        self.remove_action = QAction("Remove from Library", self)
        self.remove_action.triggered.connect(self.remove_selected_project)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.import_files_action)
        file_menu.addAction(self.import_folder_action)
        file_menu.addSeparator()
        file_menu.addAction(self.backup_action)
        file_menu.addAction(self.restore_action)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.StandardKey.Quit)

        project_menu = self.menuBar().addMenu("&Project")
        project_menu.addAction(self.open_action)
        project_menu.addAction(self.open_with_action)
        project_menu.addAction(self.reveal_action)
        project_menu.addAction(self.refresh_action)
        project_menu.addAction(self.relink_action)
        project_menu.addSeparator()
        project_menu.addAction(self.group_project_action)
        project_menu.addSeparator()
        project_menu.addAction(self.enrich_vocadb_action)
        project_menu.addAction(self.refresh_vocadb_action)
        project_menu.addAction(self.unlink_vocadb_action)
        project_menu.addSeparator()
        project_menu.addAction(self.remove_action)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Library", self)
        toolbar.setMovable(False)
        toolbar.addAction(self.import_files_action)
        toolbar.addAction(self.import_folder_action)
        toolbar.addSeparator()
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.reveal_action)
        toolbar.addAction(self.refresh_action)
        self.addToolBar(toolbar)

    def _create_panels(self) -> None:
        splitter = self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.navigation_panel = self._navigation_panel()
        splitter.addWidget(self.navigation_panel)
        splitter.addWidget(self._library_panel())
        self.inspector_panel = self._inspector_panel()
        splitter.addWidget(self.inspector_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([190, 750, 340])
        self.setCentralWidget(splitter)

    def _navigation_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(185)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        heading = QLabel("Library", panel)
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        def add_filter_label(text: str) -> None:
            lbl = QLabel(text, panel)
            lbl.setStyleSheet(
                "font-size: 11px; font-weight: 600; color: palette(text); margin-top: 5px;"
            )
            layout.addWidget(lbl)

        add_filter_label("Engine")
        self.engine_filter = QComboBox(panel)
        self.engine_filter.addItem("All engines", None)
        self.engine_filter.addItem("Synthesizer V", "svp")
        self.engine_filter.addItem("UTAU", "ust")
        self.engine_filter.addItem("Vocaloid", "vsqx")
        layout.addWidget(self.engine_filter)

        add_filter_label("Language")
        self.language_filter = QComboBox(panel)
        self.language_filter.addItem("All languages", None)
        for label, value in (
            ("Japanese", "japanese"),
            ("Chinese", "chinese"),
            ("English", "english"),
            ("Korean", "korean"),
        ):
            self.language_filter.addItem(label, value)
        layout.addWidget(self.language_filter)

        add_filter_label("Voice")
        self.voice_filter = QLineEdit(panel)
        self.voice_filter.setPlaceholderText("Any voice")
        self.voice_filter.setClearButtonEnabled(True)
        layout.addWidget(self.voice_filter)

        add_filter_label("Tuning signals")
        self.tuning_filter = QComboBox(panel)
        self.tuning_filter.addItem("Any state", None)
        self.tuning_filter.addItem("Detected", "detected")
        self.tuning_filter.addItem("None detected", "none_detected")
        self.tuning_filter.addItem("Unknown", "unknown")
        layout.addWidget(self.tuning_filter)

        add_filter_label("Versions")
        self.version_scope = QComboBox(panel)
        self.version_scope.addItem("All versions", "all")
        self.version_scope.addItem("Preferred version", "preferred")
        layout.addWidget(self.version_scope)

        add_filter_label("Health")
        self.health_filter = QComboBox(panel)
        self.health_filter.addItem("All files", None)
        self.health_filter.addItem("Available", "available")
        self.health_filter.addItem("Missing", "missing")
        self.health_filter.addItem("Unavailable", "unavailable")
        self.health_filter.addItem("Stale", "stale")
        self.health_filter.addItem("Parse failed", "failed")
        self.health_filter.addItem("Unsupported", "unsupported")
        layout.addWidget(self.health_filter)

        self.reset_filters_button = QPushButton("Reset Filters", panel)
        self.reset_filters_button.clicked.connect(self.clear_filters)
        layout.addWidget(self.reset_filters_button)

        layout.addStretch(1)
        hint = QLabel(
            "Files stay in their current locations. Importing indexes them without changing source bytes.",
            panel,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(text); font-size: 11px;")
        layout.addWidget(hint)
        return panel

    def _library_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.search_edit = QLineEdit(panel)
        self.search_edit.setPlaceholderText(
            "Search projects, aliases, voices, tags, or files…"
        )
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)

        self.table_stack = QStackedWidget(panel)

        self.project_table = ProjectTableWidget(
            0, len(self.TABLE_COLUMNS), self._project_file_for_row, self.table_stack
        )
        self.project_table.setHorizontalHeaderLabels(self.TABLE_COLUMNS)
        self.project_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.project_table.setAlternatingRowColors(True)
        self.project_table.setSortingEnabled(False)
        self.project_table.verticalHeader().setVisible(False)
        header = self.project_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.TABLE_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, 78)
        self.project_table.doubleClicked.connect(self.open_table_project)
        header.sectionClicked.connect(
            lambda column: self.project_table.sortItems(
                column, header.sortIndicatorOrder()
            )
        )
        self.table_stack.addWidget(self.project_table)

        self.empty_state_frame = QFrame(self.table_stack)
        empty_layout = QVBoxLayout(self.empty_state_frame)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(8)

        self.empty_state_title = QLabel("No projects found", self.empty_state_frame)
        self.empty_state_title.setStyleSheet(
            "font-size: 15px; font-weight: 600; color: palette(text);"
        )
        self.empty_state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_state_title)

        self.empty_state_desc = QLabel("", self.empty_state_frame)
        self.empty_state_desc.setStyleSheet("color: palette(text); font-size: 12px;")
        self.empty_state_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_desc.setWordWrap(True)
        empty_layout.addWidget(self.empty_state_desc)

        self.empty_state_action = QPushButton("Reset Filters", self.empty_state_frame)
        self.empty_state_action.setMaximumWidth(160)
        self.empty_state_action.clicked.connect(self._on_empty_state_action)
        empty_layout.addWidget(
            self.empty_state_action, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self.table_stack.addWidget(self.empty_state_frame)
        layout.addWidget(self.table_stack, 1)
        self.match_evidence = WrappingLabel("", panel)
        layout.addWidget(self.match_evidence)
        return panel

    def _inspector_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(460)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        heading = QLabel("Inspector", panel)
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        header_layout.addWidget(heading)
        layout.addLayout(header_layout)

        self.inspector_tabs = QTabWidget(panel)
        self.inspector_tabs.addTab(self._metadata_tab(self.inspector_tabs), "Metadata")
        self.inspector_tabs.addTab(
            self._versions_files_tab(self.inspector_tabs), "Versions && Files"
        )
        layout.addWidget(self.inspector_tabs)
        self.dirty_label = QLabel("", panel)
        self.dirty_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.dirty_label)
        actions = QHBoxLayout()
        self.open_file_button = QPushButton("Open file", panel)
        self.open_file_button.setStyleSheet("font-weight: 600; padding: 6px;")
        self.open_file_button.clicked.connect(self.open_selected_file)
        self.reveal_file_button = QPushButton("Reveal", panel)
        self.reveal_file_button.clicked.connect(self.reveal_selected_file)
        actions.addWidget(self.open_file_button)
        actions.addWidget(self.reveal_file_button)
        layout.addLayout(actions)
        for widget in (
            self.name_edit,
            self.description_edit,
            self.status_edit,
            self.aliases_edit,
            self.credits_edit,
            self.tags_edit,
            self.version_terms_edit,
            self.version_notes_edit,
        ):
            widget.textChanged.connect(self._update_dirty_state)
        return panel

    def _metadata_tab(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.project_summary = WrappingLabel(
            "Select a project to view its metadata.", content
        )
        layout.addWidget(self.project_summary)
        self.edit_metadata_button = QPushButton("Edit metadata", content)
        self.edit_metadata_button.setCheckable(True)
        layout.addWidget(self.edit_metadata_button)
        self.metadata_editor = QWidget(content)
        editor_layout = QVBoxLayout(self.metadata_editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.name_edit = QLineEdit(content)
        self.description_edit = MultilineEdit(content)
        self.status_edit = QLineEdit(content)
        self.aliases_edit = MultilineEdit(content)
        self.aliases_edit.setPlaceholderText("One song alias per line")
        self.credits_edit = MultilineEdit(content)
        self.credits_edit.setPlaceholderText(
            "One project credit per line: role: contributor"
        )
        self.tags_edit = QLineEdit(content)
        self.tags_edit.setPlaceholderText("Comma-separated tags")
        for title, widget in (
            ("Project name", self.name_edit),
            ("Description", self.description_edit),
            ("Workflow status", self.status_edit),
            ("Song aliases", self.aliases_edit),
            ("Project credits", self.credits_edit),
            ("Tags", self.tags_edit),
        ):
            form.addRow(title, widget)
        editor_layout.addLayout(form)
        self.save_button = QPushButton("Save project", content)
        self.save_button.clicked.connect(self.save_project)
        editor_layout.addWidget(self.save_button)
        layout.addWidget(self.metadata_editor)
        self.metadata_editor.hide()
        self.edit_metadata_button.toggled.connect(self.metadata_editor.setVisible)
        self.original_credits = WrappingLabel("", content)
        layout.addWidget(self.original_credits)
        self.group_project_button = QPushButton("Group project…", content)
        self.group_project_button.clicked.connect(self.group_selected_project)
        layout.addWidget(self.group_project_button)
        vocadb_group = QGroupBox("VocaDB", content)
        vlayout = QVBoxLayout(vocadb_group)
        self.vocadb_button = QPushButton("Find a song match…", content)
        self.vocadb_button.clicked.connect(self.enrich_selected_with_vocadb)
        vlayout.addWidget(self.vocadb_button)
        self.vocadb_refresh_button = QPushButton("Review updates", content)
        self.vocadb_refresh_button.clicked.connect(self.refresh_selected_vocadb)
        self.vocadb_unlink_button = QPushButton("Unlink", content)
        self.vocadb_unlink_button.clicked.connect(self.unlink_selected_vocadb)
        vlayout.addWidget(self.vocadb_refresh_button)
        vlayout.addWidget(self.vocadb_unlink_button)
        self.vocadb_status = WrappingLabel("", content)
        vlayout.addWidget(self.vocadb_status)
        layout.addWidget(vocadb_group)
        self.metadata_details = WrappingLabel("", content)
        self.metadata_details.hide()
        song_details = QPushButton("Song details…", content)
        song_details.clicked.connect(
            lambda: self._show_text_dialog(
                "Song metadata", self.metadata_details.text()
            )
        )
        layout.addWidget(song_details)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _versions_files_tab(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Version", content))
        row = QHBoxLayout()
        self.version_combo = QComboBox(content)
        self.version_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.version_combo.setMinimumContentsLength(8)
        self.version_combo.currentIndexChanged.connect(self._on_version_selected)
        row.addWidget(self.version_combo, 1)
        self.new_version_button = QPushButton("+", content)
        self.new_version_button.setFixedWidth(30)
        self.new_version_button.setToolTip("Create version")
        self.new_version_button.setAccessibleName("Create version")
        self.new_version_button.clicked.connect(self.create_new_version)
        row.addWidget(self.new_version_button)
        menu_button = QPushButton("More", content)
        menu_button.setFixedWidth(60)
        menu = QMenu(menu_button)
        menu.addAction("Rename version…", self.rename_selected_version)
        menu.addAction("Set as preferred", self.set_selected_preferred_version)
        menu_button.setMenu(menu)
        row.addWidget(menu_button)
        layout.addLayout(row)
        self.version_badge = QLabel("", content)
        self.version_badge.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.version_badge)
        self.set_preferred_button = QPushButton("Set as preferred", content)
        self.set_preferred_button.clicked.connect(self.set_selected_preferred_version)
        self.set_preferred_button.hide()
        self.version_terms_edit = MultilineEdit(content)
        self.version_notes_edit = MultilineEdit(content)
        self.version_terms_edit.setPlaceholderText("Distribution terms")
        self.version_notes_edit.setPlaceholderText("Version notes")
        version_details = QWidget(content)
        form = QFormLayout(version_details)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.addRow("Distribution terms", self.version_terms_edit)
        form.addRow("Notes", self.version_notes_edit)
        self.save_version_button = QPushButton("Save version", content)
        self.save_version_button.clicked.connect(self.save_selected_version)
        form.addRow(self.save_version_button)
        full_editor = QPushButton("Expand editor…", content)
        full_editor.clicked.connect(self.expand_version_editor)
        form.addRow(full_editor)
        expand = QPushButton("Notes and terms", content)
        expand.setCheckable(True)
        expand.toggled.connect(version_details.setVisible)
        layout.addWidget(expand)
        layout.addWidget(version_details)
        version_details.hide()
        layout.addWidget(QLabel("Files in this version", content))
        self.file_list = QListWidget(content)
        self.file_list.setMinimumWidth(0)
        self.file_list.setFixedHeight(100)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.file_list.currentRowChanged.connect(
            lambda row: self.file_combo.setCurrentIndex(row)
        )
        self.file_list.itemDoubleClicked.connect(self.open_selected_file)
        layout.addWidget(self.file_list)
        # Shared selection model for existing file actions and URL drag support.
        self.file_combo = QComboBox(content)
        self.file_combo.hide()
        self.file_combo.currentIndexChanged.connect(self.file_list.setCurrentRow)
        self.path_label = PathLabel(content)
        layout.addWidget(self.path_label)
        self.file_details = WrappingLabel("", content)
        layout.addWidget(self.file_details)
        self.file_problems = WrappingLabel("", content)
        layout.addWidget(self.file_problems)
        self.file_more_button = QPushButton("File actions", content)
        menu = QMenu(self.file_more_button)
        menu.addAction("Copy full path", self.copy_selected_path)
        menu.addAction("Open with…", self.open_selected_file_with)
        menu.addAction("Set as default", self.set_selected_default_file)
        menu.addAction("Move to version…", self.move_selected_file_to_version)
        self.file_more_button.setMenu(menu)
        layout.addWidget(self.file_more_button)
        self.set_default_file_button = QPushButton(content)
        self.set_default_file_button.hide()
        self.move_to_version_button = QPushButton(content)
        self.move_to_version_button.hide()
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh", content)
        self.refresh_button.clicked.connect(self.refresh_selected_file)
        self.relink_button = QPushButton("Relink…", content)
        self.relink_button.clicked.connect(self.relink_selected_file)
        row.addWidget(self.refresh_button)
        row.addWidget(self.relink_button)
        layout.addLayout(row)
        details = QPushButton("Technical details…", content)
        details.clicked.connect(self.show_file_details)
        layout.addWidget(details)
        self.drag_label = DraggableFileLabel(
            lambda: _value(self._selected_file(), "path", "locator"), content
        )
        layout.addWidget(self.drag_label)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _create_status_bar(self) -> None:
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(140)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.cancel_current_operation)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Ready")

    def _schedule_search(self) -> None:
        self.search_timer.start()

    def _filter_value(self, combo: QComboBox) -> Any:
        return combo.currentData()

    def _project_values(self):
        return (
            self.name_edit.text(),
            self.description_edit.toPlainText(),
            self.status_edit.text(),
            self.aliases_edit.toPlainText(),
            self.credits_edit.toPlainText(),
            self.tags_edit.text(),
        )

    def _version_values(self):
        return (self.version_terms_edit.text(), self.version_notes_edit.text())

    def _update_dirty_state(self):
        if self._loading_inspector:
            return
        project_dirty = (
            self._project_snapshot is not None
            and self._project_values() != self._project_snapshot
        )
        version_dirty = (
            self._version_snapshot is not None
            and self._version_values() != self._version_snapshot
        )
        self.dirty_label.setText(
            "Unsaved changes" if project_dirty or version_dirty else ""
        )
        self.save_button.setEnabled(bool(project_dirty) and not self._mutation_jobs)
        self.save_version_button.setEnabled(
            bool(version_dirty) and not self._mutation_jobs
        )
        self.edit_metadata_button.setEnabled(self._selected_project is not None)

    def _confirm_pending_edits(self):
        if self._loading_inspector:
            return True
        project_dirty = (
            self._project_snapshot is not None
            and self._project_values() != self._project_snapshot
        )
        version_dirty = (
            self._version_snapshot is not None
            and self._version_values() != self._version_snapshot
        )
        if not (project_dirty or version_dirty):
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save your changes before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            if project_dirty and not self.save_project(reload=False):
                return False
            if version_dirty and not self.save_selected_version():
                return False
        else:
            self._loading_inspector = True
            if project_dirty:
                name, desc, status, aliases, credits, tags = self._project_snapshot
                self.name_edit.setText(name)
                self.description_edit.setPlainText(desc)
                self.status_edit.setText(status)
                self.aliases_edit.setPlainText(aliases)
                self.credits_edit.setPlainText(credits)
                self.tags_edit.setText(tags)
            if version_dirty:
                terms, notes = self._version_snapshot
                self.version_terms_edit.setText(terms)
                self.version_notes_edit.setText(notes)
            self._loading_inspector = False
        self._update_dirty_state()
        return True

    def _show_text_dialog(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(650, 500)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(text)
        viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(viewer)
        close = QPushButton("Close", dialog)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def show_file_details(self):
        file = self._selected_file()
        if not file:
            return
        lines = [
            self.path_label.path,
            "",
            f"Format: {_value(file, 'format_name')} {_text(_value(file, 'format_version'))}",
            f"Parse: {_value(file, 'parse_status')}",
            f"Availability (last checked): {_value(file, 'health')}",
        ]
        lines += [
            f"{key}: {_text(value, 'Unknown')}"
            for key, value in _value(file, "details", default={}).items()
        ]
        lines += [
            "",
            "Warnings",
            *(_value(file, "warnings", default=[]) or ["None reported"]),
        ]
        self._show_text_dialog("File details", "\n".join(lines))

    def copy_selected_path(self):
        if self._selected_file():
            QApplication.clipboard().setText(self.path_label.path)
            self.statusBar().showMessage("Path copied", 2500)

    def expand_version_editor(self):
        if not self._editing_version:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Version notes and distribution terms")
        dialog.resize(650, 560)
        layout = QVBoxLayout(dialog)
        editors = []
        for title, source in (
            ("Distribution terms", self.version_terms_edit),
            ("Notes", self.version_notes_edit),
        ):
            layout.addWidget(QLabel(title, dialog))
            editor = QPlainTextEdit(dialog)
            editor.setPlainText(source.text())
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            layout.addWidget(editor)
            editors.append((editor, source))
        row = QHBoxLayout()
        cancel = QPushButton("Cancel", dialog)
        cancel.clicked.connect(dialog.reject)
        done = QPushButton("Use changes", dialog)
        done.clicked.connect(dialog.accept)
        row.addWidget(cancel)
        row.addWidget(done)
        layout.addLayout(row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for editor, source in editors:
                source.setText(editor.toPlainText())

    def rename_selected_version(self):
        if not self._editing_version or not self._confirm_pending_edits():
            return
        label, accepted = QInputDialog.getText(
            self,
            "Rename version",
            "Label",
            text=_text(_value(self._editing_version, "label")),
        )
        if accepted and label.strip():
            version_id = _value(self._editing_version, "id")
            self._start_worker(
                lambda _: self.library.update_version(version_id, label=label.strip()),
                lambda _: self.reload_projects(),
                "Renaming version…",
                mutating=True,
            )

    def _restore_layout(self):
        view = self.menuBar().addMenu("View")
        for name, widget, key in (
            ("Filters", self.navigation_panel, "filtersVisible"),
            ("Inspector", self.inspector_panel, "inspectorVisible"),
        ):
            action = QAction(name, self)
            action.setCheckable(True)
            visible = self.settings.value(key, True, type=bool)
            action.setChecked(visible)
            widget.setVisible(visible)
            action.toggled.connect(widget.setVisible)
            view.addAction(action)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("splitter")
        if state:
            self.splitter.restoreState(state)
        columns = self.settings.value("columns")
        if columns:
            self.project_table.horizontalHeader().restoreState(columns)
        self.inspector_tabs.setCurrentIndex(
            self.settings.value("inspectorTab", 0, type=int)
        )

    def _save_layout(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.settings.setValue(
            "columns", self.project_table.horizontalHeader().saveState()
        )
        self.settings.setValue("inspectorTab", self.inspector_tabs.currentIndex())
        self.settings.setValue("filtersVisible", not self.navigation_panel.isHidden())
        self.settings.setValue("inspectorVisible", not self.inspector_panel.isHidden())

    def _start_worker(
        self,
        function: Callable[[_JobContext], Any],
        on_result: Callable[[Any], None],
        activity: str,
        *,
        mutating: bool = False,
        cancellable: bool = False,
    ) -> int | None:
        if self._closing_when_idle:
            return None
        if mutating and self._mutation_jobs:
            self.statusBar().showMessage(
                "Wait for the current library operation to finish.", 5000
            )
            return None

        job_id = self._next_job_id
        self._next_job_id += 1
        cancelled = Event()
        context = _JobContext(
            job_id,
            cancelled,
            lambda current, total, path: self._worker_progress.emit(
                job_id, current, total, path
            ),
        )
        worker = _Worker(context, function, on_result)
        worker.signals.result.connect(
            self._dispatch_worker_result, Qt.ConnectionType.QueuedConnection
        )
        worker.signals.error.connect(
            self._dispatch_worker_error, Qt.ConnectionType.QueuedConnection
        )
        worker.signals.finished.connect(
            self._job_finished, Qt.ConnectionType.QueuedConnection
        )
        self._jobs[job_id] = context
        self._active_jobs = len(self._jobs)
        if mutating:
            self._mutation_jobs.add(job_id)
            self._set_mutation_controls_enabled(False)
        if cancellable:
            self._cancellable_jobs.add(job_id)
            self.cancel_button.setEnabled(True)
            self.cancel_button.show()
        self.progress.show()
        self.progress.setRange(0, 0)
        self.statusBar().showMessage(activity)
        self.thread_pool.start(worker)
        return job_id

    @Slot(int, object, object)
    def _dispatch_worker_result(
        self, job_id: int, on_result: Callable[[Any], None], result: Any
    ) -> None:
        if job_id in self._jobs and not self._closing_when_idle:
            on_result(result)

    @Slot(int, str)
    def _dispatch_worker_error(self, job_id: int, message: str) -> None:
        if job_id in self._jobs and not self._closing_when_idle:
            self._show_error(message)

    @Slot(int)
    def _job_finished(self, job_id: int) -> None:
        self._jobs.pop(job_id, None)
        self._mutation_jobs.discard(job_id)
        self._cancellable_jobs.discard(job_id)
        self._active_jobs = len(self._jobs)
        if not self._mutation_jobs and not self._closing_when_idle:
            self._set_mutation_controls_enabled(True)
        if not self._cancellable_jobs:
            self.cancel_button.hide()
        if self._active_jobs == 0:
            self.progress.hide()
            if self._closing_when_idle:
                QTimer.singleShot(0, self.close)
        elif not self._cancellable_jobs:
            self.progress.setRange(0, 0)

    @Slot(int, int, int, str)
    def _show_worker_progress(
        self, job_id: int, current: int, total: int, path: str
    ) -> None:
        if job_id not in self._jobs or self._closing_when_idle:
            return
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
            self.progress.setFormat("%v / %m")
        label = Path(path).name if path else "files"
        self.statusBar().showMessage(f"Indexing {label}…")

    @Slot()
    def cancel_current_operation(self) -> None:
        for job_id in tuple(self._cancellable_jobs):
            context = self._jobs.get(job_id)
            if context is not None:
                context.cancelled.set()
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Cancelling after the current file finishes…")

    def _set_mutation_controls_enabled(self, enabled: bool) -> None:
        for action in (
            self.import_files_action,
            self.import_folder_action,
            self.refresh_action,
            self.relink_action,
            self.restore_action,
            self.remove_action,
        ):
            action.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.relink_button.setEnabled(enabled)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.warning(self, "VocaVault", message)

    @Slot()
    def reload_projects(self, selected_id: str | None = None) -> None:
        if not self._confirm_pending_edits():
            return
        if self._editing_version:
            self._restore_version_id = _value(self._editing_version, "id")
        self.search_timer.stop()
        self._search_generation += 1
        generation = self._search_generation
        query = self.search_edit.text().strip()
        engine = self._filter_value(self.engine_filter)
        language = self._filter_value(self.language_filter)
        health = self._filter_value(self.health_filter)
        voice = self.voice_filter.text().strip() or None
        tuning = self._filter_value(self.tuning_filter)
        version_scope = self._filter_value(self.version_scope)

        def search(_context: _JobContext, *, include_fuzzy=True) -> Any:
            return self.library.list_projects(
                query=query,
                engine=engine,
                language=language,
                health=health,
                voice=voice,
                tuning=tuning,
                version_scope=version_scope,
                include_fuzzy=include_fuzzy,
            )

        def strict_results(rows):
            if generation != self._search_generation:
                return
            if rows:
                self._accept_search(generation, rows, selected_id)
            self._start_worker(
                search,
                lambda result: self._accept_search(generation, result, selected_id),
                "Finding similar names…",
            )

        if len(query) >= 3:
            self._start_worker(
                lambda context: search(context, include_fuzzy=False),
                strict_results,
                "Searching library…",
            )
        else:
            self._start_worker(
                search,
                lambda rows: self._accept_search(generation, rows, selected_id),
                "Searching library…",
            )

    def _accept_search(
        self, generation: int, rows: Any, selected_id: str | None = None
    ) -> None:
        if generation != self._search_generation:
            return
        if (
            self._project_snapshot is not None
            and self._project_values() != self._project_snapshot
        ) or (
            self._version_snapshot is not None
            and self._version_values() != self._version_snapshot
        ):
            self.statusBar().showMessage(
                "Save or discard your edits to update the search results.", 5000
            )
            return
        try:
            projects = list(rows or [])
        except TypeError:
            projects = []
        self._populate_projects(projects, selected_id=selected_id)
        count = len(projects)
        self.statusBar().showMessage(
            f"{count} project{'s' if count != 1 else ''}", 5000
        )

    def _populate_projects(
        self, projects: list[Any], selected_id: str | None = None
    ) -> None:
        if not self._confirm_pending_edits():
            return
        selected_row = self.project_table.currentRow()
        selected_item = (
            self.project_table.item(selected_row, 0) if selected_row >= 0 else None
        )
        target_selection = selected_id or (
            selected_item.data(Qt.ItemDataRole.UserRole) if selected_item else None
        )
        self.project_table.setSortingEnabled(False)
        self.project_table.blockSignals(True)
        self.project_table.setRowCount(0)
        self._projects.clear()

        for row_index, project in enumerate(projects):
            project_id = _text(_value(project, "id", "project_id"), f"row-{row_index}")
            self._projects[project_id] = project
            project_files = _files(project)
            status = _value(project, "status_name", "status", "workflow_status")
            engine = _value(project, "engine", "format_name", "file_format")
            language = _value(project, "language", "languages")
            health = _value(project, "health", "health_status", "file_health")
            file_count = _value(project, "file_count", default=len(project_files))
            values = (
                _display_name(project),
                _text(status, "—"),
                _text(engine, "—").upper(),
                _text(language, "—"),
                _text(file_count, "0"),
                _text(health, "unknown").replace("_", " "),
            )
            self.project_table.insertRow(row_index)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, project_id)
                self.project_table.setItem(row_index, column, item)

        self.project_table.setSortingEnabled(False)
        self.project_table.blockSignals(False)
        if projects:
            self.table_stack.setCurrentIndex(0)
            if target_selection and self._select_project_id(target_selection):
                return
            self.project_table.selectRow(0)
        else:
            self.table_stack.setCurrentIndex(1)
            self._update_empty_state_text()
            self._clear_inspector()

    def _has_active_filters(self) -> bool:
        return bool(
            self.search_edit.text().strip()
            or self._filter_value(self.engine_filter)
            or self._filter_value(self.language_filter)
            or self.voice_filter.text().strip()
            or self._filter_value(self.tuning_filter)
            or self._filter_value(self.health_filter)
            or (
                self._filter_value(self.version_scope)
                and self._filter_value(self.version_scope) != "all"
            )
        )

    def _update_empty_state_text(self) -> None:
        if self._has_active_filters():
            self.empty_state_title.setText("No Matching Projects")
            self.empty_state_desc.setText(
                "No projects match the current search query or active filters.\n"
                "Try clearing your search or resetting filters."
            )
            self.empty_state_action.setText("Reset Filters")
        else:
            self.empty_state_title.setText("Library is Empty")
            self.empty_state_desc.setText(
                "No project files indexed yet.\n"
                "Drag and drop files here, or click below to import."
            )
            self.empty_state_action.setText("Import Files…")

    @Slot()
    def _on_empty_state_action(self) -> None:
        if self._has_active_filters():
            self.clear_filters()
        else:
            self.import_files()

    @Slot()
    def clear_filters(self) -> None:
        self.search_edit.clear()
        self.engine_filter.setCurrentIndex(0)
        self.language_filter.setCurrentIndex(0)
        self.voice_filter.clear()
        self.tuning_filter.setCurrentIndex(0)
        self.version_scope.setCurrentIndex(0)
        self.health_filter.setCurrentIndex(0)
        self.reload_projects()

    def _project_file_for_row(self, row: int) -> str | None:
        item = self.project_table.item(row, 0)
        if not item:
            return None
        project_id = item.data(Qt.ItemDataRole.UserRole)
        project = self._projects.get(_text(project_id))
        if not project:
            return None
        files = _files(project)
        if not files:
            return None
        pref_id = _value(project, "preferred_version_id")
        for f in files:
            if _value(f, "is_default_file") and (
                _value(f, "version_id") == pref_id or not pref_id
            ):
                return _value(f, "path", "locator")
        for f in files:
            if _value(f, "version_id") == pref_id:
                return _value(f, "path", "locator")
        return _value(files[0], "path", "locator")

    def _select_project_id(self, project_id: str) -> bool:
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == project_id:
                self.project_table.selectRow(row)
                return True
        return False

    @Slot()
    def _show_selected_project(self) -> None:
        selected = self.project_table.selectedItems()
        if not selected:
            self._clear_inspector()
            return
        first_item = self.project_table.item(selected[0].row(), 0)
        project_id = first_item.data(Qt.ItemDataRole.UserRole) if first_item else None
        project = self._projects.get(_text(project_id))
        if project is None:
            self._clear_inspector()
            return

        if not self._loading_inspector and not self._confirm_pending_edits():
            self.project_table.blockSignals(True)
            self._select_project_id(_text(_value(self._selected_project, "id")))
            self.project_table.blockSignals(False)
            return
        self._clear_inspector()
        self._selection_generation += 1
        generation = self._selection_generation
        if hasattr(self.library, "database"):

            def loaded(rows):
                if generation != self._selection_generation or not rows:
                    return
                detail = rows[0]
                detail["match_field"] = project.get("match_field")
                detail["match_value"] = project.get("match_value")
                detail["matching_file_ids"] = [f["id"] for f in _files(project)]
                self._display_selected_project(detail)

            self._start_worker(
                lambda _: self.library.list_projects(project_id=project_id),
                loaded,
                "Loading project details…",
            )
        else:
            self._display_selected_project(project)

    def _display_selected_project(self, project):
        self._loading_inspector = True
        self._selected_project = project
        self.name_edit.setText(_display_name(project))
        self.description_edit.setPlainText(_text(_value(project, "description")))
        self.status_edit.setText(
            _text(
                _value(project, "status_name", "status_id", "status", "workflow_status")
            )
        )
        aliases = _value(project, "editable_aliases") or _value(
            project, "aliases", default=[]
        )
        self.aliases_edit.setPlainText("\n".join(_text(alias) for alias in aliases))
        credit_records = [
            r
            for r in _value(project, "credit_records", default=[])
            if _value(r, "scope", default="project") == "project"
        ]
        if credit_records:
            credit_lines = [
                f"{_text(_value(item, 'role'))}: {_text(_value(item, 'name'))}"
                for item in credit_records
            ]
        else:
            credit_lines = []
        self.credits_edit.setPlainText("\n".join(credit_lines))
        tags = _value(project, "tags", default=[])
        self.tags_edit.setText(", ".join(_text(tag) for tag in tags))

        # Populate versions
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        versions = project.get("versions", [])
        # Empty versions are included by the detail loader.
        preferred_id = _value(project, "preferred_version_id")
        actual_preferred_id = preferred_id
        matching_ids = project.get("matching_file_ids")
        visible_version_ids = {
            _value(f, "version_id")
            for f in _files(project)
            if matching_ids is None or _value(f, "id") in matching_ids
        }
        if preferred_id not in visible_version_ids and visible_version_ids:
            preferred_id = next(iter(visible_version_ids))
        if self._restore_version_id in visible_version_ids:
            preferred_id = self._restore_version_id
        selected_version_idx = 0
        for idx, ver in enumerate(versions):
            v_id = _value(ver, "id")
            label = _text(_value(ver, "label"), "Version")
            if v_id == actual_preferred_id:
                label += " (preferred)"
            self.version_combo.addItem(label, ver)
            if v_id == preferred_id:
                selected_version_idx = idx
        if self.version_combo.count() > 0:
            self.version_combo.setCurrentIndex(selected_version_idx)
        self.version_combo.blockSignals(False)
        self._on_version_selected()

        # VocaDB status
        vocadb_id = _value(project, "vocadb_id")
        if vocadb_id:
            self.vocadb_status.setText(f"Linked to VocaDB #{vocadb_id}")
            self.vocadb_refresh_button.setEnabled(True)
            self.vocadb_unlink_button.setEnabled(True)
        else:
            self.vocadb_status.setText("Not linked to VocaDB")
            self.vocadb_refresh_button.setEnabled(False)
            self.vocadb_unlink_button.setEnabled(False)

        aliases = _value(project, "aliases", "song_names")
        credits = _value(project, "credits", "contributors")
        links = _value(project, "links")
        tags = _value(project, "tags")
        match_field = _value(project, "match_field")
        match_value = _value(project, "match_value")
        details = []
        if match_field and match_value:
            details.append(f"Matched {match_field}: {_text(match_value)}")
        if aliases:
            details.append(f"Aliases: {_text(aliases)}")
        if credits:
            details.append(f"Credits: {_text(credits)}")
        if links:
            details.append(f"Links: {_text(links)}")
        if tags:
            details.append(f"Tags: {_text(tags)}")
        self.metadata_details.setText("\n".join(details))
        self.match_evidence.setText(
            f"Matched {match_field}: {_text(match_value)}"
            if match_field and match_value
            else ""
        )
        self.project_summary.setText(
            _display_name(project)
            + "\n"
            + (_text(_value(project, "description")) or "No description")
            + "\n"
            + "Status: "
            + (_text(_value(project, "status_name")) or "Not set")
            + "\n"
            + "Project credits: "
            + (", ".join(credit_lines) or "Not set")
        )
        original = [
            f"{r['role']}: {r['name']}"
            for r in _value(project, "credit_records", default=[])
            if r.get("scope") == "song"
        ]
        self.original_credits.setText(
            "Original song credits\n" + ("\n".join(original) or "Not set")
        )
        self._project_snapshot = self._project_values()
        self._version_snapshot = self._version_values()
        self._loading_inspector = False
        self._update_dirty_state()
        self._update_file_details()

    def _clear_inspector(self) -> None:
        self._selection_generation += 1
        self._loading_inspector = True
        self._selected_project = None
        self.name_edit.clear()
        self.description_edit.clear()
        self.status_edit.clear()
        self.aliases_edit.clear()
        self.credits_edit.clear()
        self.tags_edit.clear()
        self.version_combo.clear()
        self.version_notes_edit.clear()
        self.version_terms_edit.clear()
        self.vocadb_status.clear()
        self.vocadb_refresh_button.setEnabled(False)
        self.vocadb_unlink_button.setEnabled(False)
        self.file_combo.clear()
        self.file_details.setText("Select a project to inspect its files.")
        self.metadata_details.clear()
        self.file_list.clear()
        self.file_problems.clear()
        self.path_label.setPath("")
        self.project_summary.setText("Select a project to view its metadata.")
        self.original_credits.clear()
        self.match_evidence.clear()
        self._project_snapshot = self._version_snapshot = None
        self._editing_version = None
        self._loading_inspector = False
        self._update_dirty_state()
        self._update_file_details()

    @Slot()
    def _on_version_selected(self) -> None:
        ver = self.version_combo.currentData()
        if not self._loading_inspector and not self._confirm_pending_edits():
            self.version_combo.blockSignals(True)
            for i in range(self.version_combo.count()):
                if _value(self.version_combo.itemData(i), "id") == _value(
                    self._editing_version, "id"
                ):
                    self.version_combo.setCurrentIndex(i)
            self.version_combo.blockSignals(False)
            return
        loading = self._loading_inspector
        self._loading_inspector = True
        self._editing_version = ver
        self.version_badge.setText(
            "Preferred version" if _value(ver, "is_preferred") else ""
        )
        self.version_badge.setVisible(bool(self.version_badge.text()))
        self.version_notes_edit.setText(_text(_value(ver, "notes")))
        self.version_terms_edit.setText(_text(_value(ver, "distribution_terms")))
        self._version_snapshot = self._version_values()
        self.file_combo.blockSignals(True)
        self.file_list.blockSignals(True)
        self.file_combo.clear()
        self.file_list.clear()
        selected = 0
        for file in _files(self._selected_project):
            if ver and _value(file, "version_id") != _value(ver, "id"):
                continue
            path = _text(_value(file, "path", "locator"))
            default = _value(file, "is_default_file")
            label = Path(path).name + (" · Default" if default else "")
            item = QListWidgetItem(label)
            item.setToolTip(path)
            self.file_list.addItem(item)
            self.file_combo.addItem(label, file)
            if default:
                selected = self.file_combo.count() - 1
        self.file_combo.setCurrentIndex(selected)
        self.file_list.setCurrentRow(selected)
        self.file_list.setFixedHeight(
            min(140, max(44, self.file_list.count() * 28 + 8))
        )
        self.file_list.blockSignals(False)
        self.file_combo.blockSignals(False)
        self._loading_inspector = loading
        self._update_file_details()
        self._update_dirty_state()

    @Slot()
    def create_new_version(self) -> None:
        if not self._confirm_pending_edits():
            return
        if not self._selected_project:
            return
        project_id = _text(_value(self._selected_project, "id"))
        dlg = CreateVersionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            label = dlg.label_edit.text().strip()
            notes = dlg.notes_edit.text().strip()
            terms = dlg.terms_edit.text().strip()
            self._start_worker(
                lambda _context: self.library.create_version(
                    project_id, label, notes=notes, terms=terms
                ),
                lambda _result: self.reload_projects(project_id),
                f"Creating version '{label}'…",
                mutating=True,
            )

    @Slot()
    def set_selected_preferred_version(self) -> None:
        if not self._confirm_pending_edits():
            return
        if not self._selected_project:
            return
        ver = self.version_combo.currentData()
        if not ver:
            return
        project_id = _text(_value(self._selected_project, "id"))
        version_id = _text(_value(ver, "id"))
        self._start_worker(
            lambda _context: self.library.set_preferred_version(project_id, version_id),
            lambda _result: self.reload_projects(project_id),
            "Updating preferred version…",
            mutating=True,
        )

    @Slot()
    def save_selected_version(self) -> bool:
        version_id = _value(self._editing_version, "id")
        if not version_id:
            return True
        try:
            updated = {
                **self._editing_version,
                **self.library.update_version(
                    version_id,
                    notes=self.version_notes_edit.text(),
                    distribution_terms=self.version_terms_edit.text(),
                ),
            }
            self._editing_version = updated
            for index in range(self.version_combo.count()):
                if _value(self.version_combo.itemData(index), "id") == version_id:
                    self.version_combo.setItemData(index, updated)
                    break
            self._version_snapshot = self._version_values()
            self._update_dirty_state()
            self.statusBar().showMessage("Version saved", 3000)
            return True
        except Exception as exc:  # noqa: BLE001 - service boundary.
            self._show_error(str(exc))
            return False

    @Slot()
    def set_selected_default_file(self) -> None:
        if not self._selected_project:
            return
        file_record = self._selected_file()
        if not file_record:
            return
        file_id = _text(_value(file_record, "id", "file_id"))
        version_id = _text(_value(file_record, "version_id"))
        project_id = _text(_value(self._selected_project, "id"))
        self._start_worker(
            lambda _context: self.library.set_default_file(version_id, file_id),
            lambda _result: self.reload_projects(project_id),
            "Setting default file…",
            mutating=True,
        )

    @Slot()
    def move_selected_file_to_version(self) -> None:
        if not self._confirm_pending_edits():
            return
        if not self._selected_project:
            return
        file_record = self._selected_file()
        if not file_record:
            return
        file_id = _text(_value(file_record, "id", "file_id"))
        current_version_id = _text(_value(file_record, "version_id"))
        project_id = _text(_value(self._selected_project, "id"))
        versions = (
            self.library.list_versions(project_id)
            if hasattr(self.library, "list_versions")
            else []
        )
        if len(versions) <= 1:
            QMessageBox.information(
                self,
                "Move File",
                "This project has only one version. Create a new version first.",
            )
            return
        dlg = MoveFileToVersionDialog(versions, current_version_id, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target_version_id = dlg.selected_version_id
            if target_version_id:
                self._start_worker(
                    lambda _context: self.library.move_file_to_version(
                        file_id, target_version_id
                    ),
                    lambda _result: self.reload_projects(project_id),
                    "Moving file to version…",
                    mutating=True,
                )

    @Slot()
    def group_selected_project(self) -> None:
        if not self._confirm_pending_edits():
            return
        if not self._selected_project:
            return
        current_id = _text(_value(self._selected_project, "id"))
        current_name = _text(_value(self._selected_project, "name", "project_name"))
        self._start_worker(
            lambda _: self.library.list_grouping_candidates(current_id),
            lambda rows: self._choose_group_target(current_id, current_name, rows),
            "Loading grouping destinations…",
        )

    def _choose_group_target(self, current_id, current_name, other_projects):
        if not other_projects:
            QMessageBox.information(
                self,
                "Group Projects",
                "No other projects available in the library to group with.",
            )
            return
        dlg = GroupProjectsDialog(current_id, current_name, other_projects, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target_id = dlg.selected_target_id
            if target_id:
                self._start_worker(
                    lambda _context: self.library.group_projects(current_id, target_id),
                    lambda _result: self.reload_projects(target_id),
                    f"Grouping '{current_name}' into target project…",
                    mutating=True,
                )

    @Slot()
    def enrich_selected_with_vocadb(self) -> None:
        if not self._confirm_pending_edits():
            return
        if not self._selected_project:
            return
        dlg = VocaDbDialog(self.library, self._selected_project, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            project_id = _text(_value(self._selected_project, "id", "project_id"))
            self.reload_projects(project_id)
            self.statusBar().showMessage("VocaDB metadata applied.", 3000)

    @Slot()
    def refresh_selected_vocadb(self) -> None:
        if not self._selected_project:
            return
        if not self._confirm_pending_edits():
            return
        dlg = VocaDbDialog(self.library, self._selected_project, self, refresh=True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload_projects(_text(_value(self._selected_project, "id")))

    @Slot()
    def unlink_selected_vocadb(self) -> None:
        if not self._selected_project:
            return
        project_id = _text(_value(self._selected_project, "id"))

        def work(_context: Any) -> Any:
            return self.library.unlink_vocadb(project_id)

        def on_done(_result: Any) -> None:
            self.reload_projects(project_id)
            self.statusBar().showMessage("VocaDB link removed.", 3000)

        self._start_worker(work, on_done, "Unlinking VocaDB…", mutating=True)

    def _selected_file(self) -> Any:
        return self.file_combo.currentData() if self.file_combo.count() else None

    @Slot()
    def _update_file_details(self) -> None:
        file = self._selected_file()
        if not file:
            self.file_details.setText(
                "This version has no files. Move a file here from another version."
                if self._selected_project
                else "Select a project to inspect its files."
            )
            self.path_label.setPath("")
            self.file_problems.clear()
        else:
            self.path_label.setPath(_text(_value(file, "path", "locator")))
            tempo = _value(file, "tempo", "initial_bpm")
            tracks = _value(file, "details", default={}).get("Tracks", "Unknown")
            self.file_details.setText(
                f"{_text(_value(file, 'format_name')).upper()} · {_text(_value(file, 'parse_status')).replace('_', ' ')}\n"
                f"Tempo: {tempo if tempo is not None else 'Unknown'} · Tracks: {tracks}\n"
                f"Voice: {_text(_value(file, 'voices')) or 'Unknown'}"
            )
            issues = list(_value(file, "warnings", default=[]))
            health = _value(file, "health")
            if health not in ("available", None):
                issues.insert(0, "File status: " + _text(health))
            self.file_problems.setText("\n".join(issues[:3]))
            self.file_problems.setVisible(bool(issues))
        for widget in (
            self.open_file_button,
            self.reveal_file_button,
            self.file_more_button,
            self.refresh_button,
            self.relink_button,
        ):
            widget.setEnabled(bool(file))

    @Slot()
    def import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Index Project Files",
            "",
            "Project files (*.svp *.ust *.vsqx *.ustx *.vpr *.ccs *.ppsf);;All files (*)",
        )
        if paths:
            self._import_paths(paths)

    @Slot()
    def import_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Index Project Folder")
        if path:
            self._import_paths([path])

    def _import_paths(self, paths: list[str]) -> None:
        selected = tuple(Path(path) for path in paths)
        self._start_worker(
            lambda context: self.library.import_paths(
                selected,
                cancelled=context.cancelled.is_set,
                progress=context.report_progress,
            ),
            self._import_complete,
            "Indexing files…",
            mutating=True,
            cancellable=True,
        )

    def _import_complete(self, result: Any) -> None:
        try:
            count = len(result)
        except (TypeError, AttributeError):
            count = 1 if result is not None else 0
        failures = (
            sum(bool(item.get("error")) for item in result if isinstance(item, Mapping))
            if isinstance(result, Sequence)
            else 0
        )
        message = f"Processed {count} file{'s' if count != 1 else ''}"
        if failures:
            message += f"; {failures} failed"
        self.statusBar().showMessage(message, 6000)
        self.reload_projects()

    @Slot()
    def save_project(self, *, reload: bool = True) -> bool:
        if self._mutation_jobs:
            self.statusBar().showMessage(
                "Wait for the current library operation to finish.", 5000
            )
            return False
        project_id = _value(self._selected_project, "id", "project_id")
        if project_id is None:
            return False
        credits: list[tuple[str, str]] = []
        for line in self.credits_edit.toPlainText().splitlines():
            if not line.strip():
                continue
            role, separator, contributor = line.partition(":")
            if not separator or not role.strip() or not contributor.strip():
                self._show_error("Each credit must use 'role: contributor'.")
                return False
            credits.append((role.strip(), contributor.strip()))
        aliases = [
            line.strip() for line in self.aliases_edit.toPlainText().splitlines()
        ]
        tags = [tag.strip() for tag in self.tags_edit.text().split(",")]
        try:
            self.library.save_project_metadata(
                project_id,
                name=self.name_edit.text().strip() or "Untitled",
                description=self.description_edit.toPlainText(),
                status_id=self.status_edit.text().strip() or None,
                aliases=aliases,
                credits=credits,
                tags=tags,
            )
        except Exception as exc:  # noqa: BLE001 - service boundary.
            self._show_error(str(exc) or exc.__class__.__name__)
            return False
        self.statusBar().showMessage("Project saved", 4000)
        self._project_snapshot = self._project_values()
        self._update_dirty_state()
        if reload:
            self.reload_projects()
        return True

    @Slot()
    def refresh_selected_file(self) -> None:
        file_id = _file_id(self._selected_file())
        if file_id is None:
            self.statusBar().showMessage("Select a registered file first", 4000)
            return
        self._start_worker(
            lambda _context: self.library.refresh_file(file_id),
            lambda _: self._refresh_complete(),
            "Refreshing file metadata…",
            mutating=True,
        )

    def _refresh_complete(self) -> None:
        self.statusBar().showMessage("File metadata refreshed", 5000)
        self.reload_projects()

    @Slot()
    def relink_selected_file(self) -> None:
        file_id = _file_id(self._selected_file())
        if file_id is None:
            self.statusBar().showMessage("Select a registered file first", 4000)
            return
        path, _ = QFileDialog.getOpenFileName(self, "Relink Project File")
        if not path:
            return
        self._start_worker(
            lambda _context: self.library.relink_file(
                file_id, Path(path), require_hash_match=False
            ),
            lambda _: self._refresh_complete(),
            "Relinking file…",
            mutating=True,
        )

    @Slot()
    def open_selected_file(self) -> None:
        self._run_file_action("open_file", "Opened file")

    def open_table_project(self, index):
        item = self.project_table.item(index.row(), 0)
        if item:
            project_id = item.data(Qt.ItemDataRole.UserRole)
            self._start_worker(
                lambda _: self.library.open_project(project_id),
                lambda _: self.statusBar().showMessage("Opened preferred file", 3000),
                "Opening preferred file…",
            )

    @Slot()
    def open_selected_file_with(self) -> None:
        file_id = _file_id(self._selected_file())
        if file_id is None:
            self.statusBar().showMessage("Select a registered file first", 4000)
            return
        application, _ = QFileDialog.getOpenFileName(self, "Choose Application")
        if not application:
            return
        try:
            self.library.open_file(file_id, Path(application))
        except Exception as exc:  # noqa: BLE001 - service boundary.
            self._show_error(str(exc) or exc.__class__.__name__)
            return
        self.statusBar().showMessage("Opened file", 4000)

    @Slot()
    def reveal_selected_file(self) -> None:
        self._run_file_action("reveal_file", "Revealed file")

    def _run_file_action(self, method_name: str, success_message: str) -> None:
        file_id = _file_id(self._selected_file())
        if file_id is None:
            self.statusBar().showMessage("Select a registered file first", 4000)
            return
        try:
            getattr(self.library, method_name)(file_id)
        except Exception as exc:  # noqa: BLE001 - service boundary.
            self._show_error(str(exc) or exc.__class__.__name__)
            return
        self.statusBar().showMessage(success_message, 4000)

    @Slot()
    def backup_library(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Back Up VocaVault Metadata",
            "vocavault-backup.sqlite3",
            "SQLite database (*.sqlite3 *.db)",
        )
        if not path:
            return
        self._start_worker(
            lambda _context: self.library.backup_to(Path(path)),
            lambda _: self.statusBar().showMessage(f"Backup created at {path}", 7000),
            "Backing up metadata…",
        )

    @Slot()
    def export_library(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export VocaVault Metadata",
            "vocavault-metadata.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        self._start_worker(
            lambda _context: self.library.export_metadata(Path(path)),
            lambda _: self.statusBar().showMessage(
                f"Metadata exported to {path}", 7000
            ),
            "Exporting metadata…",
        )

    @Slot()
    def remove_selected_project(self) -> None:
        project_id = _value(self._selected_project, "id", "project_id")
        if project_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove from library",
            "Remove this project from the catalogue? Its files will remain untouched.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_worker(
            lambda _context: self.library.remove_project(project_id),
            lambda _: self._remove_complete(),
            "Removing catalogue records…",
            mutating=True,
        )

    def _remove_complete(self) -> None:
        self._selected_project = None
        self.statusBar().showMessage("Project removed; files were not changed", 6000)
        self.reload_projects()

    @Slot()
    def restore_library(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Restore VocaVault Metadata",
            "",
            "SQLite database (*.sqlite3 *.db);;All files (*)",
        )
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "Restore metadata",
            "Replace the current metadata library with this backup? Project files are not changed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_worker(
            lambda _context: self.library.restore_from(Path(path)),
            lambda _: self._restore_complete(),
            "Restoring metadata…",
            mutating=True,
        )

    def _restore_complete(self) -> None:
        self.statusBar().showMessage("Metadata restored", 6000)
        self.reload_projects()

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
            if any(
                p.is_dir() or p.suffix.lower() in SUPPORTED_EXTENSIONS for p in paths
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: Any) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        importable = [
            str(p)
            for p in paths
            if p.is_dir() or p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if importable:
            event.acceptProposedAction()
            self._import_paths(importable)
        else:
            event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_pending_edits():
            event.ignore()
            return
        self._save_layout()
        if self._active_jobs:
            self._closing_when_idle = True
            for context in self._jobs.values():
                context.cancelled.set()
            self._set_mutation_controls_enabled(False)
            self.cancel_button.hide()
            self.statusBar().showMessage("Finishing background work before closing…")
            event.ignore()
            return
        close = getattr(self.library, "close", None)
        if callable(close):
            close()
        super().closeEvent(event)

    def wait_for_workers(self) -> None:
        """Wait until every operation has released the library before app unlock."""

        self.thread_pool.waitForDone()


def apply_application_style(app: QApplication) -> None:
    """Apply small cross-platform defaults without imposing a fixed theme."""
    app.setApplicationName("VocaVault")
    app.setOrganizationName("VocaVault")
    app.setStyle("Fusion")

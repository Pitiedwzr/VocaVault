"""PySide6 user interface for the VocaVault local library."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
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
        project_menu.addAction(self.remove_action)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Library", self)
        toolbar.setMovable(False)
        toolbar.addAction(self.import_files_action)
        toolbar.addAction(self.import_folder_action)
        toolbar.addSeparator()
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.open_with_action)
        toolbar.addAction(self.reveal_action)
        toolbar.addAction(self.refresh_action)
        toolbar.addAction(self.relink_action)
        self.addToolBar(toolbar)

    def _create_panels(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._navigation_panel())
        splitter.addWidget(self._library_panel())
        splitter.addWidget(self._inspector_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([220, 700, 320])
        self.setCentralWidget(splitter)

    def _navigation_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(185)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)

        heading = QLabel("Library", panel)
        heading.setProperty("heading", True)
        layout.addWidget(heading)

        layout.addWidget(QLabel("Engine", panel))
        self.engine_filter = QComboBox(panel)
        self.engine_filter.addItem("All engines", None)
        self.engine_filter.addItem("Synthesizer V", "svp")
        self.engine_filter.addItem("UTAU", "ust")
        self.engine_filter.addItem("Vocaloid", "vsqx")
        layout.addWidget(self.engine_filter)

        layout.addWidget(QLabel("Language", panel))
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

        layout.addWidget(QLabel("Voice", panel))
        self.voice_filter = QLineEdit(panel)
        self.voice_filter.setPlaceholderText("Any voice")
        self.voice_filter.setClearButtonEnabled(True)
        layout.addWidget(self.voice_filter)

        layout.addWidget(QLabel("Tuning signals", panel))
        self.tuning_filter = QComboBox(panel)
        self.tuning_filter.addItem("Any state", None)
        self.tuning_filter.addItem("Detected", "detected")
        self.tuning_filter.addItem("None detected", "none_detected")
        self.tuning_filter.addItem("Unknown", "unknown")
        layout.addWidget(self.tuning_filter)

        layout.addWidget(QLabel("Versions", panel))
        self.version_scope = QComboBox(panel)
        self.version_scope.addItem("All versions", "all")
        self.version_scope.addItem("Preferred version", "preferred")
        layout.addWidget(self.version_scope)

        layout.addWidget(QLabel("Health", panel))
        self.health_filter = QComboBox(panel)
        self.health_filter.addItem("All files", None)
        self.health_filter.addItem("Available", "available")
        self.health_filter.addItem("Missing", "missing")
        self.health_filter.addItem("Unavailable", "unavailable")
        self.health_filter.addItem("Stale", "stale")
        self.health_filter.addItem("Parse failed", "failed")
        self.health_filter.addItem("Unsupported", "unsupported")
        layout.addWidget(self.health_filter)

        layout.addStretch(1)
        hint = QLabel(
            "Files stay in their current locations. Importing indexes them without changing source bytes.",
            panel,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        return panel

    def _library_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        self.search_edit = QLineEdit(panel)
        self.search_edit.setPlaceholderText(
            "Search projects, aliases, voices, tags, or files…"
        )
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)

        self.project_table = QTableWidget(0, len(self.TABLE_COLUMNS), panel)
        self.project_table.setHorizontalHeaderLabels(self.TABLE_COLUMNS)
        self.project_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.project_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.project_table.setAlternatingRowColors(True)
        self.project_table.setSortingEnabled(True)
        self.project_table.verticalHeader().setVisible(False)
        header = self.project_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.TABLE_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.project_table.doubleClicked.connect(self.open_selected_file)
        layout.addWidget(self.project_table, 1)
        return panel

    def _inspector_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(440)
        layout = QVBoxLayout(panel)

        layout.addWidget(QLabel("Inspector", panel))
        form = QFormLayout()
        self.name_edit = QLineEdit(panel)
        self.description_edit = QPlainTextEdit(panel)
        self.description_edit.setMaximumHeight(100)
        self.status_edit = QLineEdit(panel)
        self.status_edit.setPlaceholderText("Workflow status")
        self.aliases_edit = QPlainTextEdit(panel)
        self.aliases_edit.setMaximumHeight(80)
        self.aliases_edit.setPlaceholderText("One song alias per line")
        self.credits_edit = QPlainTextEdit(panel)
        self.credits_edit.setMaximumHeight(90)
        self.credits_edit.setPlaceholderText("One credit per line: role: contributor")
        self.tags_edit = QLineEdit(panel)
        self.tags_edit.setPlaceholderText("Comma-separated tags")
        form.addRow("Project", self.name_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Status", self.status_edit)
        form.addRow("Aliases", self.aliases_edit)
        form.addRow("Credits", self.credits_edit)
        form.addRow("Tags", self.tags_edit)
        layout.addLayout(form)

        self.save_button = QPushButton("Save Project", panel)
        self.save_button.clicked.connect(self.save_project)
        layout.addWidget(self.save_button)

        line = QFrame(panel)
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        self.file_combo = QComboBox(panel)
        layout.addWidget(self.file_combo)
        self.file_details = QLabel("Select a project to inspect its files.", panel)
        self.file_details.setWordWrap(True)
        self.file_details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.file_details)

        button_row = QHBoxLayout()
        open_button = QPushButton("Open", panel)
        open_button.clicked.connect(self.open_selected_file)
        reveal_button = QPushButton("Reveal", panel)
        reveal_button.clicked.connect(self.reveal_selected_file)
        self.refresh_button = QPushButton("Refresh", panel)
        self.refresh_button.clicked.connect(self.refresh_selected_file)
        self.relink_button = QPushButton("Relink…", panel)
        self.relink_button.clicked.connect(self.relink_selected_file)
        button_row.addWidget(open_button)
        button_row.addWidget(reveal_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.relink_button)
        layout.addLayout(button_row)

        self.metadata_details = QLabel(panel)
        self.metadata_details.setWordWrap(True)
        self.metadata_details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.metadata_details)
        layout.addStretch(1)
        return panel

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
    def reload_projects(self) -> None:
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

        def search(_context: _JobContext) -> Any:
            return self.library.list_projects(
                query=query,
                engine=engine,
                language=language,
                health=health,
                voice=voice,
                tuning=tuning,
                version_scope=version_scope,
            )

        self._start_worker(
            search,
            lambda rows: self._accept_search(generation, rows),
            "Searching library…",
        )

    def _accept_search(self, generation: int, rows: Any) -> None:
        if generation != self._search_generation:
            return
        try:
            projects = list(rows or [])
        except TypeError:
            projects = []
        self._populate_projects(projects)
        count = len(projects)
        self.statusBar().showMessage(
            f"{count} project{'s' if count != 1 else ''}", 5000
        )

    def _populate_projects(self, projects: list[Any]) -> None:
        selected_id = _text(_value(self._selected_project, "id", "project_id"))
        self.project_table.setSortingEnabled(False)
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

        self.project_table.setSortingEnabled(True)
        if selected_id and self._select_project_id(selected_id):
            return
        if projects:
            self.project_table.selectRow(0)
        else:
            self._clear_inspector()

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

        self._selected_project = project
        self.name_edit.setText(_display_name(project))
        self.description_edit.setPlainText(_text(_value(project, "description")))
        self.status_edit.setText(
            _text(
                _value(project, "status_name", "status_id", "status", "workflow_status")
            )
        )
        aliases = _value(project, "editable_aliases", default=[])
        self.aliases_edit.setPlainText("\n".join(_text(alias) for alias in aliases))
        credit_records = _value(project, "editable_credit_records", default=[])
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

        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for file_record in _files(project):
            file_path = _text(
                _value(file_record, "path", "locator", "file_path"), "Unnamed file"
            )
            label = Path(file_path).name or file_path
            self.file_combo.addItem(label, file_record)
        self.file_combo.blockSignals(False)

        aliases = _value(project, "aliases", "song_names")
        credits = _value(project, "credits", "contributors")
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
        if tags:
            details.append(f"Tags: {_text(tags)}")
        self.metadata_details.setText("\n".join(details))
        self._update_file_details()

    def _clear_inspector(self) -> None:
        self._selected_project = None
        self.name_edit.clear()
        self.description_edit.clear()
        self.status_edit.clear()
        self.aliases_edit.clear()
        self.credits_edit.clear()
        self.tags_edit.clear()
        self.file_combo.clear()
        self.file_details.setText("Select a project to inspect its files.")
        self.metadata_details.clear()

    def _selected_file(self) -> Any:
        return self.file_combo.currentData() if self.file_combo.count() else None

    @Slot()
    def _update_file_details(self) -> None:
        file_record = self._selected_file()
        if file_record is None:
            self.file_details.setText(
                "No registered file is available for this project."
            )
            return
        path = _text(
            _value(file_record, "path", "locator", "file_path"), "Unknown path"
        )
        file_format = _text(
            _value(file_record, "format_name", "engine", "file_format"), "unknown"
        )
        format_version = _text(_value(file_record, "format_version"), "unknown")
        parse_status = _text(
            _value(file_record, "parse_status", "status"), "not parsed"
        )
        health = _text(_value(file_record, "health", "health_status"), "unknown")
        tempo = _value(file_record, "tempo", "initial_bpm", "bpm")
        voice = _value(file_record, "voice", "voice_name", "voices")
        lines = [
            path,
            f"Format: {file_format} {format_version}",
            f"Parse: {parse_status.replace('_', ' ')}",
            f"Health (last checked): {health.replace('_', ' ')}",
        ]
        if tempo is not None:
            lines.append(f"Tempo: {_text(tempo)}")
        if voice:
            lines.append(f"Voice: {_text(voice)}")
        warnings = _value(file_record, "warnings", "parse_warnings")
        if warnings:
            lines.append(f"Warnings: {_text(warnings)}")
        details = _value(file_record, "details", "parse_details")
        if details:
            if isinstance(details, Mapping):
                detail_text = ", ".join(
                    f"{key}: {_text(value)}" for key, value in details.items()
                )
            else:
                detail_text = _text(details)
            lines.append(f"Details: {detail_text}")
        self.file_details.setText("\n".join(lines))

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
    def save_project(self) -> None:
        if self._mutation_jobs:
            self.statusBar().showMessage(
                "Wait for the current library operation to finish.", 5000
            )
            return
        project_id = _value(self._selected_project, "id", "project_id")
        if project_id is None:
            return
        credits: list[tuple[str, str]] = []
        for line in self.credits_edit.toPlainText().splitlines():
            if not line.strip():
                continue
            role, separator, contributor = line.partition(":")
            if not separator or not role.strip() or not contributor.strip():
                self._show_error("Each credit must use 'role: contributor'.")
                return
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
            return
        self.statusBar().showMessage("Project saved", 4000)
        self.reload_projects()

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

    def closeEvent(self, event: QCloseEvent) -> None:
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
    app.setStyleSheet(
        """
        QLabel[heading="true"] { font-size: 18px; font-weight: 600; }
        QLineEdit, QComboBox, QPlainTextEdit { padding: 4px; }
        QTableWidget { border: 0; }
        """
    )

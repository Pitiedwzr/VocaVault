from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel

from vocavault.library import LibraryService
from vocavault.ui import DraggableFileLabel, MainWindow, create_file_drag


def test_create_file_drag_constructs_valid_mime_data(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    dummy_file = tmp_path / "song.svp"
    dummy_file.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')

    source_widget = QLabel("source")
    drag = create_file_drag(source_widget, dummy_file)

    mime_data = drag.mimeData()
    assert mime_data.hasUrls()
    urls = mime_data.urls()
    assert len(urls) == 1
    assert Path(urls[0].toLocalFile()) == dummy_file.resolve()
    assert Path(mime_data.text()) == dummy_file.resolve()

    # Ensure source file is completely untouched
    assert dummy_file.exists()
    assert dummy_file.stat().st_size > 0


def test_draggable_file_label_wording_and_interaction(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    dummy_file = tmp_path / "test.ust"
    dummy_file.write_text("[#SETTING]\nTempo=120\n", encoding="utf-8")

    label = DraggableFileLabel(lambda: str(dummy_file))
    assert label.text() == "Drag files into supported applications"
    assert label.toolTip() == "Drag files into supported applications"


def test_project_table_resolves_preferred_version_file(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    library = LibraryService(tmp_path / "drag_test.sqlite3")

    f1 = tmp_path / "lead.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    library.import_paths([f1])

    window = MainWindow(library)
    window.thread_pool.waitForDone()
    app.processEvents()

    assert window.project_table.rowCount() == 1
    resolved_path = window._project_file_for_row(0)
    assert resolved_path is not None
    assert Path(resolved_path).resolve() == f1.resolve()

    window.thread_pool.waitForDone()
    app.processEvents()
    window.close()
    app.processEvents()

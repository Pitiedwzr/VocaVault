import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from threading import Event
from unittest.mock import patch

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea, QWidget

from vocavault.library import LibraryService
from vocavault.ui import MainWindow, VocaDbDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def settle(widget, app):
    for _ in range(5):
        assert widget.thread_pool.waitForDone(5000)
        app.processEvents()


def add(library, root, name):
    path = root / (name + ".svp")
    path.write_text('{"version":113,"time":{},"tracks":[]}')
    return library.import_paths([path])[0]


def test_preferred_version_default_open_and_switch_are_synchronized(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    first = add(lib, tmp_path, "first")
    second = add(lib, tmp_path, "second")
    lib.group_projects(second["project_id"], first["project_id"])
    lib.set_preferred_version(first["project_id"], second["version_id"])
    window = MainWindow(lib)
    settle(window, app)
    assert window.file_combo.count() == 1
    assert window._selected_file()["id"] == second["file_id"]
    with patch.object(lib, "open_file") as opened:
        window.open_selected_file()
        opened.assert_called_once_with(second["file_id"])
    window.version_combo.setCurrentIndex(0)
    assert window._selected_file()["id"] == first["file_id"]
    # Empty versions remain selectable, and disable file actions.
    lib.create_version(first["project_id"], "Empty revision")
    window.reload_projects()
    settle(window, app)
    window.version_combo.setCurrentIndex(2)
    assert window._selected_file() is None
    assert not window.open_file_button.isEnabled()
    window.close()


def test_search_relevance_and_unfiltered_inspector(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    add(lib, tmp_path, "Ghost Rule")
    other = add(lib, tmp_path, "Zzz Remix")
    lib.set_aliases(other["project_id"], ["Ghost Rules"])
    window = MainWindow(lib)
    settle(window, app)
    window.search_edit.setText("Ghost Rule")
    window.reload_projects()
    settle(window, app)
    assert window.project_table.item(0, 0).text() == "Ghost Rule"
    assert "Matched project name: Ghost Rule" == window.match_evidence.text()
    window.close()


def test_cancel_switch_keeps_draft_and_save_targets_original_version(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    first = add(lib, tmp_path, "first")
    revision = lib.create_version(first["project_id"], "Revision")
    window = MainWindow(lib)
    settle(window, app)
    window.version_notes_edit.setText("Important draft")
    with patch(
        "vocavault.ui.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Cancel,
    ):
        window.version_combo.setCurrentIndex(1)
    assert window.version_combo.currentIndex() == 0
    assert window.version_notes_edit.text() == "Important draft"
    with patch(
        "vocavault.ui.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Save,
    ):
        window.version_combo.setCurrentIndex(1)
    versions = lib.list_versions(first["project_id"])
    assert versions[0]["notes"] == "Important draft"
    assert not versions[1]["notes"]
    assert window.version_combo.currentData()["id"] == revision["id"]
    window.close()


def test_enrichment_worker_keeps_gui_available_and_ignores_cancelled_results(
    tmp_path, app
):
    lib = LibraryService(tmp_path / "ui.db")
    started, release = Event(), Event()
    observed = []

    def search(_):
        observed.append(QThread.currentThread())
        started.set()
        release.wait(5)
        return [{"id": 1, "name": "Late result"}]

    with patch.object(lib, "search_vocadb_candidates", side_effect=search):
        dialog = VocaDbDialog(lib, {"id": "test", "name": "Query"})
        try:
            assert started.wait(2)
            assert observed[0] != app.thread()
            dialog.reject()
        finally:
            release.set()
            settle(dialog, app)
    assert dialog.candidates_table.rowCount() == 0


def test_refresh_only_fetches_until_user_confirms(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    with (
        patch.object(
            lib, "preview_vocadb_refresh", return_value={"id": 1, "name": "New title"}
        ),
        patch.object(lib, "apply_vocadb_enrichment") as apply,
    ):
        dialog = VocaDbDialog(lib, {"id": "test", "name": "Manual title"}, refresh=True)
        settle(dialog, app)
        assert "Manual title" in dialog.preview_text.text()
        assert "New title" in dialog.preview_text.text()
        assert not dialog.name_check.isChecked()
        apply.assert_not_called()
        dialog.reject()


def test_inspector_long_text_does_not_require_horizontal_scroll(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    record = add(lib, tmp_path, "Long name " + "x" * 100)
    lib.update_version(
        record["version_id"],
        label="Revision " + "y" * 200,
        notes="notes " * 300,
        distribution_terms="terms " * 300,
    )
    window = MainWindow(lib)
    window.resize(900, 560)
    window.show()
    settle(window, app)
    for index in range(2):
        window.inspector_tabs.setCurrentIndex(index)
        if index == 0:
            window.edit_metadata_button.setChecked(True)
        app.processEvents()
        scroll = window.inspector_tabs.widget(index)
        assert isinstance(scroll, QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0, (
            index,
            scroll.viewport().width(),
            [
                (type(child).__name__, child.minimumSizeHint().width())
                for child in scroll.widget().findChildren(QWidget)
                if child.minimumSizeHint().width() > scroll.viewport().width() - 40
            ],
        )
        assert scroll.widget().width() <= scroll.viewport().width()
    assert window.open_file_button.isVisible()
    window.close()


def test_layout_preferences_round_trip(tmp_path, app):
    lib = LibraryService(tmp_path / "ui.db")
    window = MainWindow(lib)
    settle(window, app)
    window.navigation_panel.hide()
    window.inspector_tabs.setCurrentIndex(1)
    window.close()
    restored = MainWindow(lib)
    settle(restored, app)
    assert restored.navigation_panel.isHidden()
    assert restored.inspector_tabs.currentIndex() == 1
    restored.close()

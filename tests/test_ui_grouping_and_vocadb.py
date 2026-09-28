from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from vocavault.library import LibraryService
from vocavault.ui import (
    CreateVersionDialog,
    GroupProjectsDialog,
    MainWindow,
    MoveFileToVersionDialog,
    VocaDbDialog,
)
from vocavault.vocadb import VocaDbCandidate, VocaDbNetworkError


@pytest.fixture
def app_instance() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_create_version_dialog_validation(app_instance: QApplication) -> None:
    dlg = CreateVersionDialog()
    assert dlg.label_edit.text() == ""

    # Attempting to accept with empty label shows warning and rejects
    with mock.patch("vocavault.ui.QMessageBox.warning") as mock_warn:
        dlg._validate_and_accept()
        mock_warn.assert_called_once()
        assert dlg.result() != CreateVersionDialog.DialogCode.Accepted

    # With valid label
    dlg.label_edit.setText("Harmony Mix")
    dlg.notes_edit.setText("Lead and harmony")
    dlg.terms_edit.setText("CC-BY")
    dlg._validate_and_accept()
    assert dlg.result() == CreateVersionDialog.DialogCode.Accepted


def test_group_projects_dialog(app_instance: QApplication) -> None:
    other_projects = [
        {"id": "p2", "name": "Target Song", "files": [{"path": "a.svp"}]},
        {"id": "p3", "name": "Another Song", "files": []},
    ]
    dlg = GroupProjectsDialog("p1", "Source Song", other_projects)
    assert dlg.project_combo.count() == 2
    assert dlg.selected_target_id == "p2"


def test_move_file_to_version_dialog(app_instance: QApplication) -> None:
    versions = [
        {"id": "v1", "label": "Default"},
        {"id": "v2", "label": "Revision 2"},
    ]
    dlg = MoveFileToVersionDialog(versions, "v1")
    assert dlg.version_combo.count() == 1
    assert dlg.selected_version_id == "v2"


def test_vocadb_dialog_search_preview_and_apply(tmp_path: Path, app_instance: QApplication) -> None:
    library = LibraryService(tmp_path / "ui_vocadb.sqlite3")
    f1 = tmp_path / "song.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    imported = library.import_paths([f1])
    proj = library.list_projects()[0]

    mock_cand = VocaDbCandidate(
        id=12345,
        name="Ghost Rule",
        artist_string="DECO*27 feat. Hatsune Miku",
        song_type="Original",
        names=({"value": "ゴーストルール", "language": "Japanese"},),
        artists=({"name": "DECO*27", "roles": "Composer"},),
        links=({"kind": "youtube", "url": "https://youtube.com/watch?v=xxx", "label": "YouTube"},),
    )

    with mock.patch.object(library, "search_vocadb_candidates", return_value=[mock_cand]):
        dlg = VocaDbDialog(library, proj)
        assert dlg.candidates_table.rowCount() == 1
        assert dlg.selected_candidate is not None
        assert dlg.apply_button.isEnabled()
        assert "DECO*27" in dlg.preview_text.text()

        with mock.patch.object(library, "apply_vocadb_enrichment") as mock_apply:
            dlg._apply_enrichment()
            mock_apply.assert_called_once()


def test_vocadb_dialog_offline_resilience(tmp_path: Path, app_instance: QApplication) -> None:
    library = LibraryService(tmp_path / "ui_vocadb_offline.sqlite3")
    proj = {"id": "p1", "name": "Offline Project"}

    with mock.patch.object(library, "search_vocadb_candidates", side_effect=VocaDbNetworkError("Connection refused")):
        dlg = VocaDbDialog(library, proj)
        assert dlg.candidates_table.rowCount() == 0
        assert "Could not reach VocaDB" in dlg.status_label.text()
        assert not dlg.apply_button.isEnabled()


def test_main_window_version_and_grouping_integration(tmp_path: Path, app_instance: QApplication) -> None:
    library = LibraryService(tmp_path / "ui_integration.sqlite3")
    f1 = tmp_path / "lead.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    f2 = tmp_path / "harmony.ust"
    f2.write_text("[#SETTING]\nTempo=120\n", encoding="utf-8")
    library.import_paths([f1, f2])

    window = MainWindow(library)
    window.thread_pool.waitForDone()
    app_instance.processEvents()

    assert window.project_table.rowCount() == 2

    # Group project 2 into project 1
    p1 = window.project_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    p2 = window.project_table.item(1, 0).data(Qt.ItemDataRole.UserRole)

    with mock.patch("vocavault.ui.GroupProjectsDialog") as mock_dlg_cls:
        mock_dlg = mock_dlg_cls.return_value
        mock_dlg.exec.return_value = CreateVersionDialog.DialogCode.Accepted
        mock_dlg.selected_target_id = p1

        window.project_table.selectRow(1)
        window.group_selected_project()
        window.thread_pool.waitForDone()
        app_instance.processEvents()

    window.thread_pool.waitForDone()
    app_instance.processEvents()

    # Now there is 1 grouped project with 2 files
    assert window.project_table.rowCount() == 1
    grouped_proj = library.list_projects()[0]
    assert len(grouped_proj["files"]) == 2

    window.thread_pool.waitForDone()
    app_instance.processEvents()
    window.close()
    app_instance.processEvents()


def test_main_window_vocadb_enrichment_flow(tmp_path: Path, app_instance: QApplication) -> None:
    library = LibraryService(tmp_path / "ui_vocadb_e2e.sqlite3")
    f1 = tmp_path / "ghost.svp"
    f1.write_bytes(b'{"version": 113, "time": {}, "tracks": []}')
    library.import_paths([f1])

    window = MainWindow(library)
    window.thread_pool.waitForDone()
    app_instance.processEvents()

    assert window.project_table.rowCount() == 1
    assert window.project_table.item(0, 0).text() == "ghost"
    assert window.name_edit.text() == "ghost"

    mock_cand = VocaDbCandidate(
        id=12345,
        name="ゴーストルール",
        artist_string="DECO*27 feat. 初音ミク",
        song_type="Original",
        names=({"value": "Ghost Rule", "language": "English"},),
        artists=({"name": "DECO*27", "roles": "Composer"},),
        links=({"kind": "youtube", "url": "https://youtube.com/watch?v=xxx", "label": "YouTube"},),
    )

    with mock.patch.object(library, "search_vocadb_candidates", return_value=[mock_cand]):
        # Simulate user triggering enrichment dialog and clicking apply
        dlg = VocaDbDialog(library, window._selected_project, window)
        dlg.candidates_table.selectRow(0)
        dlg._apply_enrichment()
        assert dlg.result() == VocaDbDialog.DialogCode.Accepted

        # Notify main window as done in enrich_selected_with_vocadb
        project_id = window.project_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        window.reload_projects(project_id)
        window.thread_pool.waitForDone()
        app_instance.processEvents()

    # The project name must now be enriched!
    assert window.project_table.item(0, 0).text() == "ゴーストルール"
    assert window.name_edit.text() == "ゴーストルール"

    # Aliases and credits must now appear in the inspector
    assert "Ghost Rule" in window.aliases_edit.toPlainText()
    assert "Composer: DECO*27" in window.credits_edit.toPlainText()
    assert "Linked to VocaDB #12345" in window.vocadb_status.text()
    assert "YouTube: https://youtube.com/watch?v=xxx" in window.metadata_details.text()

    window.thread_pool.waitForDone()
    app_instance.processEvents()
    window.close()
    app_instance.processEvents()


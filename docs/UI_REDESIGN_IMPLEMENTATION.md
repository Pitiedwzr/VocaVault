# UI Redesign: Implementation Log

This document records the UI improvements applied after the v0.2 release,
targeting the crowding and discoverability issues identified in the UI plan.

## Changes implemented

### Phase 1 – Inspector tabs and scroll areas (critical)

The right-hand inspector panel previously stacked more than 25 controls into a
fixed-width column with no scroll area, causing controls below the visible area
to be permanently clipped at typical screen heights.

- Replaced the flat inspector `QVBoxLayout` with a `QTabWidget` containing two
  focused tabs:
  - **Metadata** tab: project name, description, workflow status, aliases,
    credits, tags, Save Project button, and a `QGroupBox` for VocaDB enrichment
    (Enrich, Refresh, Unlink, status label).
  - **Versions & Files** tab: version selector, distribution terms, version
    notes, Set Preferred / Save Version actions (in a `QGroupBox`), then a Files
    `QGroupBox` with the file picker, Set Default / Move to Version, file detail
    label, Open / Reveal / Refresh / Relink buttons (split across two rows), and
    the draggable file export handle.
- Each tab's content widget is wrapped in a `QScrollArea` with
  `setWidgetResizable(True)`, ensuring all controls are reachable regardless
  of window or panel height.
- "Save Project" and "Save Version" are now visually isolated in separate tabs,
  eliminating the save-confusion problem.
- The inspector minimum width was increased from 270 px to 280 px and the
  maximum from 440 px to 460 px to give the tab bar room to breathe. Default
  splitter allocation adjusted to `[210, 680, 390]`.
- File action buttons split from a single cramped four-button row into two
  paired rows: Open / Reveal on the first row, Refresh / Relink on the second.

### Phase 2 – Empty state feedback and navigation cleanup

**Empty state widget in the library panel**

- The library panel now uses a `QStackedWidget` (`table_stack`) containing two
  pages: index 0 is the project table, index 1 is an empty state frame.
- When a search or filter combination returns zero results, `_populate_projects`
  switches to the empty state page and fills in context-sensitive text:
  - With active filters: "No Matching Projects" + prompt to reset filters.
  - With no filters: "Library is Empty" + prompt to import.
- An action button in the empty state calls `clear_filters()` or
  `import_files()` depending on the context.

**Navigation sidebar labels**

- Replaced the full-height `QLabel` above each filter control with a compact
  styled label (`font-size: 11px; font-weight: 600; color: palette(mid)`) via a
  local `add_filter_label()` helper. This recovers approximately 60 px of
  vertical space in the sidebar.
- Added `layout.setContentsMargins(10, 10, 10, 10)` and `setSpacing(4)` for
  tighter, more intentional spacing.
- Added a **Reset Filters** button at the bottom of the filter list, connected
  to the new `clear_filters()` slot.
- Bottom hint text reduced to `font-size: 11px` to match the label style.

### Phase 3 – Toolbar streamlining

Removed seven actions from the toolbar that duplicate menu bar entries or
inspector buttons: Open with…, Relink, Group with Project…, and Enrich with
VocaDB…. The toolbar now holds only the high-frequency primary actions:
Import Files…, Import Folder…, Open, Reveal, and Refresh.

All removed toolbar actions remain accessible through the **Project** menu and
the inspector panel buttons.

### Phase 4 – Inbound drag-and-drop and styling polish

**Inbound drag-and-drop import**

- `MainWindow.__init__` now calls `self.setAcceptDrops(True)`.
- `dragEnterEvent` accepts drops that include at least one directory or a file
  with a supported extension (`.svp .ust .vsqx .ustx .vpr .ccs .ppsf .mid`),
  as defined by the new `SUPPORTED_EXTENSIONS` module-level constant.
- `dragMoveEvent` accepts all URL-based moves while the drag is over the window.
- `dropEvent` filters URLs to importable paths and calls `_import_paths()` for
  any that qualify; unrecognised drops are cleanly ignored.

**Application stylesheet expansion**

Expanded `apply_application_style()` with additional rules that follow the OS
palette and do not hard-code light/dark colours:

- Tab pane border with slight radius on lower corners.
- Tab bar padding and bold font on the selected tab.
- `QGroupBox` with border, corner radius, and indent-corrected title.
- Consistent `QPushButton` padding.

## New symbols introduced

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `SUPPORTED_EXTENSIONS` | module constant | `frozenset` of importable file suffixes, shared between `dragEnterEvent` and `dropEvent` |
| `MainWindow.inspector_tabs` | attribute | `QTabWidget` that replaces the old flat inspector layout |
| `MainWindow.table_stack` | attribute | `QStackedWidget` toggling between the project table and empty state |
| `MainWindow.empty_state_frame` | attribute | Empty-state container page inside `table_stack` |
| `MainWindow.empty_state_title` | attribute | Primary heading label in the empty state |
| `MainWindow.empty_state_desc` | attribute | Secondary description label in the empty state |
| `MainWindow.empty_state_action` | attribute | Context-sensitive action button in the empty state |
| `MainWindow.reset_filters_button` | attribute | Reset Filters button in the sidebar |
| `MainWindow._metadata_tab()` | method | Builds the Metadata tab content in a `QScrollArea` |
| `MainWindow._versions_files_tab()` | method | Builds the Versions & Files tab content in a `QScrollArea` |
| `MainWindow._has_active_filters()` | method | Returns `True` if any filter or search is non-default |
| `MainWindow._update_empty_state_text()` | method | Fills empty state labels based on filter state |
| `MainWindow._on_empty_state_action()` | slot | Routes empty-state button click to reset or import |
| `MainWindow.clear_filters()` | slot | Resets all filter controls to their default values |
| `MainWindow.dragEnterEvent()` | override | Accepts drags containing importable files or folders |
| `MainWindow.dragMoveEvent()` | override | Keeps the drag action alive as it moves over the window |
| `MainWindow.dropEvent()` | override | Imports dropped project files and folders |

## Existing symbols unchanged

All widget attribute names (`name_edit`, `description_edit`, `status_edit`,
`aliases_edit`, `credits_edit`, `tags_edit`, `save_button`, `vocadb_button`,
`vocadb_refresh_button`, `vocadb_unlink_button`, `vocadb_status`,
`version_combo`, `new_version_button`, `version_terms_edit`,
`version_notes_edit`, `set_preferred_button`, `save_version_button`,
`file_combo`, `set_default_file_button`, `move_to_version_button`,
`file_details`, `refresh_button`, `relink_button`, `drag_label`,
`metadata_details`) are preserved with the same types and signal connections.
All 92 existing tests pass without modification.

## Verification

- **Platform**: Windows, Python 3.13.5, PySide6 ≥ 6.8.
- **Test suite**: 92 passed, 0 failed, 0 errors (`uv run pytest`).
- Offscreen Qt smoke and integration tests confirm all widget attributes remain
  accessible and all signal/slot connections function correctly.
- No backend changes: `library.py`, `database.py`, `models.py`, and all parser
  modules are untouched.

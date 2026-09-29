# v0.2 implementation and validation

This records the initial implementation. The subsequent review found correctness
and validation gaps; see [the follow-up log](V0_2_FOLLOWUP.md) for the fixes,
current evidence, and remaining release limitations.

This is the second milestone release of VocaVault, implementing the retrieval,
external enrichment, project grouping, and editor integration requirements
specified in `PLAN.md`.

## Implemented

- **UST & VSQX Project Parsers**:
  - Read-only `.ust` parser supporting Shift-JIS/CP932 and UTF-8 encodings,
    tempo parameters, note lengths, lyrics, voicebank directories, and pitch/vibrato
    signal extraction.
  - Read-only `.vsqx` XML parser supporting Vocaloid3 (`vsq3`) and Vocaloid4
    (`vsq4`) namespace schemas, multi-track setups, tempos, singer declarations,
    lyrics, and pitch/dynamics curve evidence.
  - Resource bounds and read-stability validation match SVP adapter standards.
- **Full-Text and Typo Retrieval**:
  - SQLite FTS5 index (`search_fts`) with trigram tokenization covering project
    names, song aliases, credits, contributor aliases, tags, statuses, voicebanks,
    and version labels.
  - Substring fallback for 1- and 2-character queries, including CJK characters
    (e.g., `幽霊`).
  - Independent RapidFuzz vocabulary retrieval for typo tolerance when queries
    produce zero strict FTS hits (e.g., `gosht rul` -> `ゴーストルール`).
  - Ranking hierarchy: exact title/alias matches > prefix > substring > fuzzy >
    filename. Literal search-box punctuation handling prevents SQL/FTS syntax injection.
  - Multi-condition filters (engine, voice, language, tuning) bound to the same file.
- **Confirmed VocaDB Metadata Enrichment**:
  - `VocaDbClient` with HTTPS queries to `https://vocadb.net/api`, SQLite response
    caching (`api_cache`), and rate-limit / timeout / error handling.
  - Interactive candidate search, preview, and confirmation dialog (`VocaDbDialog`)
    with selectable field updates (display name, aliases, original credits, media links).
  - Enrichment provenance tracking (`source_type = 'vocadb'`, `source_identifier`).
  - Song-level enrichment strictly preserves project-level tuner credits, distribution
    terms, and user overrides.
  - Offline resilience: API failures present informative messages without blocking
    local catalogue operations.
- **Version Management and Project Grouping**:
  - Schema migration 3 adding `distribution_terms` to `versions`.
  - Version lifecycle management (`create_version`, `list_versions`, `update_version`,
    `delete_version`, `set_preferred_version`, `set_default_file`, `move_file_to_version`,
    `move_version_to_project`).
  - Project grouping (`group_projects`) preserving all version records, distribution
    terms, notes, project tags, credits, links, and overrides with sort order collision
    safety.
  - UI version selector, version notes/terms editor, default file selector, and
    "Group into Project…" dialog.
- **Outbound Editor Drag-and-Drop**:
  - Outbound drag support on project table rows and inspector file chips using
    `QDrag` and `QMimeData.setUrls()`.
  - Copy action semantics (`Qt.DropAction.CopyAction`); source files are never deleted
    or moved by external drops.
  - Standardized interface guidance: "Drag files into supported applications".

## Parser evidence

| Format | Fixture evidence | Handling |
| --- | --- | --- |
| SVP schemas 113, 134, 153 | 9 supplied files; synthetic regression | JSON; tempo map, tracks, voices, lyrics, tuning evidence |
| UST (Version 1.2) | Synthetic fixtures (Shift-JIS & UTF-8) | INI sections; tempo, notes, voicebank directory, tuning evidence |
| VSQX (Vocaloid 3 & 4) | Synthetic fixtures (vsq3 / vsq4 schemas) | XML; tempo, tracks, parts, singers, lyrics, pitch/dynamics curves |
| MIDI, USTX, VPR, CCS, PPSF | Opaque registration path | Indexed without technical metadata parsing (scheduled later) |

## Verification and test results

Validation was conducted on Windows with Python 3.13.5 and dependencies locked
in `uv.lock`.

Local test suite summary: **90 passed tests** covering:
- Database schema migrations (v1 -> v2 -> v3) and atomic rollback.
- SVP, UST, and VSQX parser syntax, limits, and encoding resilience.
- Search acceptance criteria from Section 5 of `PLAN.md`:
  - `ghost` alias resolution for Japanese songs.
  - `gosht rul` typo recall with zero FTS matches.
  - Short CJK substring query path (`幽霊`).
  - Full-width unicode normalization and hyphenated titles (`ＧＨＯＳＴ`, `ghost-rule`).
  - Contributor alias explanation (`miku` -> Hatsune Miku).
  - Multi-field filter binding to the same physical file.
- VocaDB candidate querying, caching, rate-limit handling, and enrichment override safety.
- Version CRUD, preferred version rules, default file assignment, and grouping metadata preservation.
- Outbound drag MIME URL construction and non-destructive copy semantics.
- Qt GUI smoke and integration flows in offscreen mode.

## Remaining release gates (v0.3 and beyond)

- Managed storage root with byte copying, SHA-256 staging, and bundle verification.
- ZIP archive entry inspection, directory traversal prevention, and staged extraction.
- Dependency reference validation between project files and audio assets.
- Cross-platform packaging verification on macOS and clean-machine environments.

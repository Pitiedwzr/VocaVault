# VocaVault: Product and Implementation Plan

Status: proposed implementation baseline, revised 2026-09-26. Features and performance targets below are planned, not validated capabilities. Review decisions are recorded in [PLAN_REVIEW_SYNTHESIS.md](PLAN_REVIEW_SYNTHESIS.md).

## 1. Product Scope

VocaVault is a local desktop library for virtual singer producers, tuners, cover artists, and mixers. It connects songs, credits, production projects, saved versions, and physical files so users can find work by title, alias, contributor, voicebank, format, or tag and open it in their editor.

Target platforms are Windows and macOS. The application uses Python, PySide6, and SQLite. Core library operations work offline; VocaDB enrichment is optional and initiated by the user.

Product principles:

- User edits take precedence over imported metadata. Show the source and uncertainty of extracted information.
- Indexing and parsing are read-only. Managed imports initially copy files and preserve source material.
- Songs, projects, versions, and files have stable identities independent of names and paths.
- Support both externally indexed and managed files in the same library.
- Make unsupported formats and missing files visible without preventing manual cataloguing.
- Preserve the original scope of multilingual search, optional managed storage, version grouping, and editor integration while delivering them in stages.

Outside the initial scope: audio synthesis, project conversion or rewriting, automatic translation, cloud synchronization, collaboration, automatic version snapshots/diffs, and automatic detection of installed voicebanks. Grouping saved versions does not provide backup or undo of edits made in an external editor.

## 2. Data Model and Ownership

### 2.1 Core entities

```text
Song (optional association while a project is unidentified)
  └── Project: one cover, arrangement, or independently authored production
        └── Version: a labelled set of saved files
              └── File (one or many: editor project, audio, README, etc.)
                    └── Parse observation when applicable to that file
                          └── Track/part summaries and dependency references
```

| Entity | Owns | Important rules |
| --- | --- | --- |
| Song | Display name, language-labelled names/aliases, original credits, original media links, optional VocaDB ID | May have many independent projects; matching titles alone never merge songs. |
| Project | User-facing name, optional song association, production credits, description, tags, workflow status, preferred version | Logical grouping independent of physical folders; an unidentified song does not block import. |
| Version | Project association, label, explicit ordering, notes, distribution links and terms, optional credit overrides | May contain several editor files, such as separate UTAU lead and harmony files. No version tree or snapshot storage initially. |
| File | Version association, role, stable ID, path locator, management mode, detected format/version, size, modification time, content hash | A physical location is registered once; identical bytes at different locations remain separate records. Select a default file for opening each version. |
| Parse observation | File ID, content hash, parser ID/version, parsing time, status, extracted values, warnings, track/part summaries and references | Technical metadata belongs to the observed file content, not globally to its project. |
| Storage root | Root ID, location, availability, default import policy | Start with one managed vault and multiple indexed locations; file management mode remains explicit. |

Create a default version during initial import, even before the grouping UI is available. Do not infer that files in one ZIP or with similar names are versions of the same project. Present import candidates separately; grouping is an explicit user choice.

A registered file belongs to one version initially. Shared external audio may be referenced by several project files through dependency records without registering it repeatedly as an owned file. Reassigning an existing file to another version requires an explicit grouping action; it does not move its bytes.

Registered files remain editable in their external editors. On change, mark the old parse observation stale and update it after a successful rescan. Retaining an old hash or version label does not retain the old bytes. A new saved file can be added as a separate version; silently overwriting a file cannot be undone by VocaVault.

### 2.2 Names, credits, terms, and user values

- Store aliases as related `song_names` rows with original text, normalized search text, optional language/script, name kind, and source. Select one row as the display name; do not duplicate it as an extra alias just to mark it primary. Romanization is a name kind/script distinction, not a language by itself.
- Store multiple contributors and roles using `contributors`, `song_credits`, `project_credits`, and, when needed, `version_credits`. Contributor names need not be unique. Keep composers, lyricists, arrangers, tuners, and mixers distinct.
- Keep original-song links separate from project download/distribution links. Terms belong to the downloaded version, with any file-specific exceptions retained. Preserve verbatim notes, source URLs, and supplied README/license files; optional labels such as “credit required” are summaries entered by the user.
- Store tags relationally. Workflow statuses are editable labels with optional display order; do not enforce a production state machine. File-health issues are separate from workflow status.
- Keep parser observations and VocaDB responses separate from user overrides. Explicitly distinguish “no override” from “user intentionally cleared this field.” Rescans and API refreshes cannot replace overrides or resurrect aliases the user dismissed.
- Record source type, source identifier/URL, observation time, and parser version where relevant. Effective values use user overrides first, then the appropriate source for the field: project-file tempo comes from parsing, original credits from a confirmed song match. Do not substitute original-song BPM for arrangement tempo.

### 2.3 Database implementation boundaries

Initial tables cover `songs`, `song_names`, contributors/credits, `projects`, `versions`, `files`, `storage_roots`, `parse_observations`, `file_tracks`, tags, workflow statuses, and explicit ownership tables for links and overrides. Add dependency reference records with adapter support, and API cache, import journal, and saved searches with their milestones.

Use foreign keys, transactions, schema migrations, and indexes on ownership and filter columns. Enforce that a preferred version belongs to its project and a default opening file belongs to its version. Use concrete foreign keys for owned links/metadata rather than unvalidated `(entity_type, entity_id)` references. Cascading database removal must never imply physical file deletion.

JSON is appropriate for bounded parser details, tempo maps, or cached API responses. Names, credits, tags, and fields used for filtering need queryable relational representations. Search indexes are derived data and must be rebuildable from the library.

## 3. Parser Architecture and Metadata Semantics

### 3.1 Parser contract

Implement built-in format adapters behind a small common interface: `detect` examines a bounded header/container and returns format/version evidence; `parse` returns a structured observation with capabilities, values, warnings, and references. A runtime third-party plugin system is unnecessary initially.

Parsing runs outside the GUI thread. Limit file size, nesting, decompressed bytes, and execution time; disable external XML entities/network access and unsafe YAML object construction. Parser failures affect that file only. Never execute bundled scripts, load voicebanks, or rewrite project files during extraction.

Track states independently: `not_parsed`, `parsed`, `partial`, `unsupported`, and `failed`. Unsupported or unreadable properties remain unknown. Cache observations by file content hash and parser version; hash stable bytes and reject/retry results if the source changes during reading.

Cache extracted reference strings with the observation, but resolve their locations separately. Relinking, copying, or relocating a bundle must re-resolve references even when its content hash has not changed.

### 3.2 Format support roadmap

These are targets. A format/version is advertised as parse-supported only after representative fixtures pass; filename extensions alone do not establish support.

| Format | Planned handling | Metadata parsing milestone |
| --- | --- | --- |
| `.svp` | JSON-based adapter; validate actual serialization/version variants against samples | First complete adapter in v0.1 |
| `.ust` | Text adapter with tested encoding and dialect handling, including legacy Japanese encodings | v0.2 |
| `.vsqx` | XML adapter with tested namespace/version handling | v0.2 |
| `.ustx` | YAML adapter; validate track/part and tempo handling | Post-v0.3, before v1.0 where fixtures permit |
| `.vpr` | Container adapter; inspect required JSON members without unpacking the project as a distribution ZIP | Post-v0.3, before v1.0 where fixtures permit |
| `.ccs`, `.ppsf` | Catalogue as opaque files until adapters and representative versions are validated | Later; not blockers for v1.0 |
| Distribution `.zip` | Staged import of selected project bundles and supporting files | v0.3 |

All listed project formats may be indexed with manual metadata from v0.1. This does not imply parsing, conversion, or editor compatibility. Record a tested matrix of file format/version, editor version that produced it, encoding, extracted fields, and known limitations.

OpenUtau documents USTX as YAML with tracks, parts, and tempo changes. UtaFormatix's VPR reader locates project JSON inside a ZIP container. These are useful adapter references, not evidence that VocaVault already supports every variant. [USTX format](https://github.com/openutau/OpenUtau/wiki/USTX-file-format), [VPR reader](https://raw.githubusercontent.com/sdercolin/utaformatix3/master/core/src/main/kotlin/core/io/Vpr.kt)

### 3.3 Extracted fields

| Field | Stored meaning and display |
| --- | --- |
| Tempo | Initial/minimum/maximum BPM, whether changes exist, change count, and optional tempo map with explicit time units/resolution. Unknown values remain null. |
| Tracks and voices | Distinguish vocal and audio tracks. Store voicebank identifiers/names and declared languages at track or part level as supported; display a deduplicated summary. Missing IDs do not prove a voicebank is unavailable. |
| Tuning signals | Per supported signal: `unknown`, `none_detected`, or `detected`, with counts/evidence for pitch, vibrato, dynamics, etc. Detection does not claim manual tuning, completeness, or quality. |
| Tuning assessment | Optional user judgement: `unknown`, `none`, `partial`, or `tuned`. Keep it separate from detected signals. |
| Lyrics | Counts of eligible vocal notes and populated lyric fields, plus uncertainty. A repeated vowel can be intentional; populated-field coverage is not a completion score. Optional user assessment: `unknown`, `none`, `partial`, or `complete`. |
| Language | Allow several values. Distinguish declared/project language from alias language and uncertain inference; manual assignment is available. |
| References | Original dependency reference, resolution base, resolved path when possible, and `available`, `missing`, `unavailable`, or `unknown` state. |

Do not sum track counts across alternate versions. A multi-file version initially shows per-file summaries; any aggregate must state exactly which files it includes.

## 4. File Lifecycle and Storage

### 4.1 External indexing

Index selected files/folders in place and retain their absolute locators plus optional root association. Record paths separately from stable file IDs. Respect actual filesystem case sensitivity and Unicode behavior; do not lowercase paths as a universal identity rule.

Import is idempotent for an already registered location. A different path with the same SHA-256 is a duplicate-content candidate, not proof of the same project, credits, or physical file. Offer keep separately or skip; do not automatically merge or delete. Size and modification time help detect changes but are not content identity.

Provide refresh and relink actions. Search for relink candidates only in user-selected locations, using hashes as evidence; require a user selection if ambiguous or content differs. An offline drive is unavailable, not evidence of deletion. Do not remove records when paths cannot be read.

### 4.2 Managed copies and dependencies

Offer `Index in place` and `Copy to Vault` per import when managed mode ships. A setup preference supplies the default. Mixed libraries are supported; automatic moves/source deletion are deferred.

For managed files, store a root ID and a relative path. Start with a stable bundle directory such as `<vault>/<project-id>/<version-id>/` and preserve the selected bundle's internal filenames/layout. Relocating the whole vault updates its root after validation instead of rewriting every record.

Preview candidate editor files, supporting files, resolved dependencies, expected bytes, and unresolved references before copying. Preserve relative dependencies by copying their selected common layout. Do not chase arbitrary references outside the selected bundle automatically. Absolute references can still point to the original location after copying: show that limitation and offer indexing or user repair in the editor. Do not label a bundle self-contained unless every supported dependency check passed; unknown dependencies remain visible.

Use a journaled import sequence:

1. Create an import operation ID, record the intended destination, and validate available space and resolved selected paths. Reject destination escapes through existing links/reparse points; directory scans must not follow links outside the selected bundle implicitly.
2. Stage copies on the vault filesystem, preserving the bundle structure and applying bounded parsing.
3. Verify file sizes and hashes against stable source content. A changing source requires retry or cancellation.
4. Publish the staged bundle to a unique destination without overwriting an existing bundle; record progress in the journal.
5. Commit library records and search updates, then mark the operation complete.

Filesystem publication and SQLite commit are separate operations. On restart, reconcile incomplete journal entries and staged/published manifests; finish registration or offer removal of verified app-owned incomplete copies. Never delete the source as recovery. Cancellation before publication discards only that operation's staging files; after publication it enters recovery rather than pretending nothing was written.

### 4.3 ZIP imports and folder templates

Distribution ZIPs require entry inspection and staging. Reject traversal paths, absolute/drive paths, symlinks and other special entries, normalized-name collisions, and destination escapes. Enforce entry-count, expanded-byte, nesting, and compression-ratio limits while streaming; do not rely only on archive headers. Report encrypted/unsupported archives without partial success. Do not recursively unpack nested archives or execute contents. Internal VPR members stay inside the original container.

For ZIPs, `Index in place` means index an already extracted folder. Do not register temporary extracted paths as durable files. Keep the original archive untouched; offer a managed extraction or ask the user to extract it to a permanent location first.

Custom folder templates arrive after basic managed imports. Use documented tokens such as `$engine`, `$original_author`, `$title`, and `$language`, defined fallbacks/multi-value behavior, platform-safe components, length limits, and an ID suffix for collisions. Templates affect the bundle's outer directory, preserving its internal layout. Metadata edits update the database only; explicit reorganize actions preview all moves and use the same recovery mechanism. Automatic relocation is deferred.

### 4.4 Removal, health, and backup

`Remove from library` removes catalogue associations only. Any later physical deletion action must be separate, limited to verified managed paths, list affected files, and use recoverable trash where supported. Indexed files are never deleted by library removal.

Basic health indicators cover missing/unavailable files, stale or failed parses, and unsupported versions from the first release. Add dependency checks when supported by an adapter and duplicate-content review with managed storage. Show “not checked” for unsupported checks; avoid a global healthy badge that implies the editor can render the project.

Provide consistent database backup/restore and metadata export from v0.1, with a backup before schema migration. A metadata backup does not include project bytes. With managed storage, a full-vault backup includes a consistent database snapshot, root mapping, manifest, and managed file copies. Pause app writes and verify files did not change during capture; retry or mark the backup incomplete if an external editor changes them. Restore the database and files into new locations, verify the manifest, and then select the restored library/root. Indexed file records remain in the metadata, but their bytes are excluded unless explicitly included in an export.

## 5. Search and Filtering

### 5.1 Behavior

Search display names, aliases, project names, contributor names/aliases, voicebank names/aliases, tags, and filenames. Paths are optional lower-priority search fields. A query for `miku` matches Japanese-only text only when a known alias relationship connects it; normalization does not translate names.

Maintain original display strings and separate normalized search strings using NFKC, casefold, whitespace normalization, and tested punctuation handling. Do not merge catalogue identities because their normalized strings match.

Default results are projects with the matching version/file identified. Filters across engine, language, and tuning must match the same file, not unrelated files in one project. A project-level tag/status can combine with that file match. Provide explicit `All versions` and `Preferred version` scopes so older matching versions are not hidden accidentally. Grouping multiple hits must preserve the strongest matching evidence.

### 5.2 Implementation

For v0.1, use normalized alias-aware search and SQL filters against the initial library size. Introduce FTS5 and RapidFuzz in v0.2 after measuring the baseline.

- FTS5 provides fast token/prefix retrieval; evaluate a trigram index for substring search. Provide a literal substring fallback for one- and two-character queries, including Japanese/Chinese input. Trigram full-text queries cannot match substrings shorter than three characters. [SQLite FTS5](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)
- Typo retrieval must have a path independent of strict FTS matches. At the target library size, compare normalized queries against the filtered name/alias vocabulary in a background task, then map matches to files/projects. Later optimization may use approximate candidate generation only if recall tests still pass. RapidFuzz ranks only the choices supplied to it. [RapidFuzz process API](https://rapidfuzz.github.io/RapidFuzz/Usage/process.html)
- Union exact/prefix/substring results with fuzzy candidates, deduplicate, and rank with exact title/alias matches ahead of fuzzy or filename matches. Do not truncate the fuzzy input to the first arbitrary FTS results.
- Treat search-box punctuation as literal text unless an explicit advanced query mode is added. Parameterize SQL and safely construct FTS expressions; SQL parameterization alone does not make raw FTS query syntax literal.
- Maintain derived indexes when names, credits, overrides, grouping, or parse observations change. Rebuilds must preserve library metadata. Debounce searches and discard results from superseded queries.

### 5.3 Acceptance examples

| Query/setup | Required result |
| --- | --- |
| `ghost`, with stored English aliases for both Japanese songs | Finds Ghost Rule and Go Go Ghost Ship through their aliases. |
| `gosht rul`, with no exact FTS hit | Finds Ghost Rule through independent fuzzy candidate retrieval. |
| `幽霊` | Finds ゴーゴー幽霊船 through the short-query substring path. |
| `ＧＨＯＳＴ` or `ghost-rule` | Finds the expected normalized title without changing its displayed spelling. |
| `miku`, with a stored Hatsune Miku/初音ミク association | Finds relevant voicebank/contributor matches and explains the matched field. |
| Engine=SVP and voice=Teto, where only an unrelated UST file uses Teto | Does not manufacture a match by combining different files. |
| Edited alias followed by index rebuild | New alias remains searchable; a removed/dismissed alias does not reappear. |

## 6. Optional VocaDB Enrichment

Search candidates using locally chosen title/author text. Show names, credited artists, media links, and VocaDB ID for disambiguation. The user confirms a candidate and reviews proposed fields before applying changes. Matching scores, if shown, are local heuristics rather than verified identity probabilities.

Store the external ID, fetched response/time, and provenance of accepted fields. Refresh presents changes and respects manual edits and dismissed values. Song-level enrichment must not overwrite tuner credits or distribution terms. Handle missing/merged external entries without deleting local data, and permit unlinking/reselecting a match.

VocaDB exposes limited name categories, including a non-English category historically labelled Japanese in the API. Preserve the provider category separately; do not infer that every such name is Japanese or promise translations for every language. Request structured names when importing individual aliases. Cache responses, identify the client, and avoid repeated requests on library views. These choices follow the [VocaDB public API documentation](https://wiki.vocadb.net/docs/public-api).

Use HTTPS, bounded timeouts, capped retries/backoff, and cancellation. Respect rate-limit responses. Cached/manual metadata remains usable offline. Send only the selected query text/IDs; do not upload project files, local paths, or the library.

## 7. Desktop UI and Editor Integration

Use a three-panel layout: navigation/filters on the left, a sortable project table in the center, and an editable inspector on the right. Support dark and light themes, keyboard navigation, visible focus, Unicode text/fonts, and persistent column/layout preferences. Project rows expand to versions/files when grouping is introduced.

The inspector distinguishes song, project, version, and file information and labels extracted values and user overrides. Show import progress, per-file failures, missing paths, offline drives, and recovery actions. Bulk imports and searches must leave navigation responsive.

Provide `Open`, `Open with…`, and `Reveal in Explorer/Finder`. A project-level open uses its preferred version/default file; prompt if there is no unambiguous target. Validate that the file exists, allow editor associations, and pass paths as arguments without shell command interpolation. Opening a file delegates editing to the selected external application.

For outbound drag-and-drop, use local file URLs through `QDrag` and `QMimeData.setUrls()`. Offer copy semantics; never remove source files because an external target requested a move. Qt supports URL MIME data, but target applications decide whether and how to accept a drop. Use the wording “Drag files into supported applications.” [Qt MIME data](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QMimeData.html), [Qt drag-and-drop behavior](https://doc.qt.io/qtforpython-6/overviews/qtgui-dnd.html)

Prototype real editor drops in the feasibility stage. Record OS, editor version, file format, and observed behavior; distinguish “opened project” from “imported track.” Keep launch/reveal fallbacks where drops are unsupported. No conversion is implied.

Smart Folders, when added, are saved search/filter definitions evaluated against current records. They do not move physical files.

## 8. Application Architecture and Delivery

Keep the implementation a single desktop application with clear modules:

| Component | Responsibility |
| --- | --- |
| Qt UI/models | Display, user input, selection, progress, and presentation of results |
| Library services | Import/grouping/editing rules, effective metadata, relinking, editor launch |
| Storage service | Managed copies, manifests, recovery journal, backup/restore, path validation |
| Parser adapters | Read-only extraction into the shared observation contract |
| Search service | Normalization, filters, retrieval, ranking, and index rebuilds |
| Enrichment client | VocaDB requests, cache, candidate selection data, and proposed changes |
| SQLite repositories | Constraints, transactions, migrations, and consistent persistence |

Use background workers for file I/O, parsing, hashing, search, and HTTP; exchange results through Qt signals. Workers do not mutate widgets or share a live SQLite connection across threads. Serialize writes through a dedicated persistence path; keep transactions short and batch index updates. Enforce a single application writer per library in the initial release.

Store the live database in the OS application-data directory, separate from the managed vault. Do not support a live database on a network share or concurrently synchronized folder initially. Persist logs locally with actionable error messages and redact paths/query text in any user-shared diagnostics.

Choose and pin supported Python/PySide6/SQLite versions during the feasibility stage, including FTS5 availability in packaged builds. Create a reproducible dependency lock and a minimal packaged app for both target platforms early. Choose the packaging tool from that spike; validate installers, Unicode paths, clean-machine startup without Python, and upgrades before each platform release. Record dependency notices and the distribution/signing approach before publishing installers. Do not claim macOS support solely from Windows tests.

## 9. Milestones and Release Gates

Milestones are ordered by dependency, not calendar promises. Each release advertises only its validated formats and platforms.

| Stage | Deliverable | Exit evidence |
| --- | --- | --- |
| Feasibility | Representative sample corpus; model examples; one SVP parser slice; multilingual search prototype; outbound drag prototype; minimal Windows/macOS packages | Record variants and extraction limits; demonstrate two projects for one song and a multi-file version; record actual editor-drop behavior and packaging gaps. |
| v0.1: usable local library | External indexing, manual names/aliases/credits/tags/statuses, default versions, SVP extraction, alias/substring search, filters, open/reveal, refresh/relink, basic health, metadata backup/restore | Import/search/edit/restart offline; preserve source hashes; unsupported files remain cataloguable; restore metadata; test Unicode paths and unavailable drives. No managed copying or ZIP extraction yet. |
| v0.2: retrieval and grouping | UST/VSQX adapters, FTS5/fuzzy retrieval, confirmed VocaDB enrichment, version grouping/default selection, tested editor drag support | Search cases in section 5 pass, including no-FTS-hit typos; grouping preserves credits/terms; refresh preserves overrides; API errors do not block local work. |
| v0.3: managed vault | Verified copy imports, ZIP staging, dependency preview, duplicate-content review, recovery journal, root relocation, full-vault backup/restore | Interrupt every import boundary and recover without overwriting sources/destinations; reject escaping archives; preserve supported relative references; restore a usable bundle. |
| v1.0: release hardening | Folder templates and explicit reorganize, Smart Folders, validated additional adapters, accessibility and platform packaging polish | Complete regression/performance checks and clean-machine checks on each advertised OS; unsupported adapters remain explicitly deferred. |

One complete parser is sufficient for v0.1. Aliases and basic health stay in the first release because they deliver the central search workflow and make external indexing usable. Folder automation, advanced workflow rules, extra formats, installed-voicebank checks, snapshots, and automatic source deletion do not block the initial library.

## 10. Validation and Performance Targets

Use representative, permission-cleared or synthetic fixtures rather than production libraries. Include each supported format/version, encoding variants, defaults versus non-default tuning signals, multiple voices/tempos, malformed files, truncated files, unsupported versions, and changed-during-read cases. Check that parsing/indexing leaves source bytes unchanged.

Integration scenarios must cover migrations with pre-upgrade backup, metadata restore, stale-index rebuilds, conflicting enrichment, same-name/different-song projects, repeated imports, editor saves, relinking, offline/removable storage, and deletion semantics. Managed-storage tests additionally cover disk-full/permission errors, collisions, unsafe archive entries, cancellation, and restart recovery. Test GUI progress and cancellation on large batches.

Initial benchmark target: 10,000 projects, 50,000 file records, and 100,000 searchable names/aliases on a documented reference laptop with a local SSD. Aim for p95 exact/filter search within 200 ms and fuzzy results within 500 ms after debounce, with no long work on the UI thread. Measure cold/warm behavior and short CJK queries separately. These are proposed budgets: the feasibility benchmark must confirm or revise them with recorded evidence before implementation choices are treated as settled. Return no misleadingly complete results after an undisclosed candidate cutoff.

Before a platform release, record supported OS/architecture and editor versions, parser capability matrix, search recall examples, benchmark hardware/results, import-recovery outcomes, and backup/restore outcomes. Test scope expands with shipped features, not with every deferred idea.

## 11. Example Workflow After v0.3

1. A user imports a ZIP containing a Japanese UTAU cover, separate lead/harmony USTs, audio, and a README. The preview lists candidates, dependencies, and any unresolved references.
2. They group the selected files into one project/version and choose `Copy to Vault`. VocaVault stages and verifies the bundle, registers it, and leaves the downloaded ZIP untouched.
3. Supported parsers provide per-file technical summaries. The user retains the README terms, records the tuner, and optionally confirms a VocaDB song match to add aliases and original credits.
4. Months later, `ghost` finds the project through its stored English alias; `gosht rul` can find it through fuzzy retrieval. A matching version/file is shown.
5. They open a selected UST using a configured compatible editor, or drag it to an editor/version validated to accept that drop. VocaVault does not convert the file.
6. After the editor saves, refresh updates extracted observations while preserving manual metadata. A separately saved revision can be added to the same project as another version.

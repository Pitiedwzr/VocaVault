# Assessment of PLAN_REVIEW_A and Plan Revision Decisions

Reviewed 2026-09-26 against the original PLAN.md and the earlier project review. This assessment covers planning quality and checked technical references; no application implementation or performance has been validated.

## Verdict

Review A is useful architectural input. Its strongest recommendations address real weaknesses: separating song/project/file identities, preserving metadata provenance, handling multi-track and tempo data, keeping parsers read-only, and staging managed imports. Most agree with the earlier review.

It should not be adopted verbatim. Some suggested features increase scope without clear first-release value, and the proposed search pipeline and sample schema need correction. The revised [PLAN.md](PLAN.md) makes the accepted ideas explicit and adds lifecycle, recovery, testing, and delivery criteria.

## Recommendations and Decisions

| Review A sections | Assessment | Decision in the revised plan |
| --- | --- | --- |
| 1, 16: separate songs/projects/files/versions; group projects in the UI | Strong and directly useful. The review alternates between Asset → Revision, Revision belonging to Asset/Project, and a merged file tree, leaving ownership unresolved. | Use Song → Project → Version → Files. A version can contain multiple editor files and supporting material. Start with a default version; introduce grouping UI later. |
| 2: relational aliases | Useful for this search-heavy library, although JSON arrays are not universally invalid in SQLite. The example also duplicates the primary name and treats Romaji as a language. | Related name rows with language/script/kind/source, one selected display name, separate normalized search text. Keep JSON for parser details and API caches. |
| 3: provenance and confirmed VocaDB matching | Strong. Refresh must preserve user edits, and cached responses are appropriate. | Keep user overrides separate, including intentional clearing and dismissed aliases. Confirm a candidate and preview fields; preserve credits and terms at their proper level. |
| 4, 5: FTS5 plus RapidFuzz, separate aliases from fuzzy matching | Correct distinction and sensible Python tooling, but the sequential pipeline has a recall gap: a strict FTS query can exclude the intended answer before fuzzy ranking sees it. | Add independent fuzzy retrieval over the filtered vocabulary, union with exact results, and explicitly test a typo that produces no FTS hit. Add a short-CJK substring path. |
| 6, 7: richer tuning/lyrics states | Recognizes a real ambiguity, but “substantial,” “complete,” and coverage percentages can imply knowledge parsers do not have. | Store observable signals and counts separately from optional user assessments. Unknown/unsupported is distinct from absence. |
| 8, 9: tempo changes and per-track voices | Useful, but the sample `project_tracks` table stores observations too broadly: versions of a project can have different tracks. | Attach track/part/tempo observations to specific file content and parser versions. Preserve multiple voices and languages. |
| 10, 20: parser adapters and read-only parsing | Strong. A small adapter contract is sufficient; extensible third-party plugin loading is not necessary. | Built-in adapters, bounded parsing, capabilities and warnings, fixture-based support matrix. One complete adapter first. |
| 11: ZIP staging and validation | Essential for the proposed import workflow, but staging alone does not define crash recovery or database/filesystem consistency. | Add bounded extraction, manifests, journaled publication, restart reconciliation, and stable paths for imported files. |
| 12, 14: stable IDs, mixed management, metadata-based folders | Strong foundation. Automatic relocation and multiple policy layers add complexity prematurely. | Mixed indexed/managed records, one managed root initially, relative managed paths, metadata-only edits, later explicit previewed reorganize. |
| 13: hashes and duplicate detection | Useful evidence, but equal bytes establish content equality, not the same physical file, project, authorship, or version lineage. | SHA-256 plus stable IDs; no automatic merge/deletion. Paths and modification times are observations rather than identities. |
| 15: native drag-and-drop with fallbacks | Sound and appropriately qualifies target-editor support. | Early editor/OS compatibility spike, file URLs, copy semantics, launch/reveal fallbacks. No implicit format conversion. |
| 17: make status a workflow | Custom labels are useful. Enforced transitions are optional product expansion. | Editable status labels/order, with health tracked separately; defer workflow automation. |
| 18: health checks | Useful for missing files and parse failures; installed voicebank detection would require additional engine-specific integration. Duplicate content is not necessarily a fault. | Ship basic health early; add supported dependency checks and duplicate review later. Preserve “not checked” states. |
| 19: proposed schema | Helpful sketch, not an implementation-ready schema. It omits explicit revision ownership and the project credits/terms discussed elsewhere; generic entity references also lack direct foreign-key enforcement. | Specify ownership, default-selection constraints, concrete foreign keys, source precedence, and queryable metadata. Add tables only as milestones require them. |
| 21: reduce v1 scope | Strong advice, but delaying aliases defers the central multilingual value, while three parsers in the first release increase implementation risk. | Keep manual aliases and basic health in v0.1; ship one validated parser, then expand. Probe cross-platform packaging and editor drops early. |

## Technical Checks and Caveats

- The review's `[1]` through `[6]` reference labels have no link definitions in the supplied file. This makes its evidence difficult to verify as delivered. Direct references checked for this revision appear below.
- VocaDB documents limited name categories and recommends caching. Its non-English category is labelled Japanese in the API; that category should not be treated as a reliable language tag for every name. [VocaDB API documentation](https://wiki.vocadb.net/docs/public-api)
- FTS5 supports token/prefix search and optional trigram substring search; trigram full-text queries do not match substrings shorter than three characters. RapidFuzz scores supplied choices, so ranking alone cannot recover an excluded candidate. The recall-gap conclusion is an inference from those documented behaviors. [SQLite FTS5](https://www.sqlite.org/fts5.html), [RapidFuzz process API](https://rapidfuzz.github.io/RapidFuzz/Usage/process.html)
- OpenUtau documents USTX as YAML with tempo changes and track/part data. UtaFormatix's VPR reader uses a ZIP container and finds `Project/sequence.json` or its backslash variant. Review A's VPR clarification is useful background, but the original PLAN.md did not actually claim VPR was plain JSON. [USTX format](https://github.com/openutau/OpenUtau/wiki/USTX-file-format), [VPR reader](https://raw.githubusercontent.com/sdercolin/utaformatix3/master/core/src/main/kotlin/core/io/Vpr.kt)
- Qt documents URL MIME data and target-controlled drop handling. This supports the proposed implementation mechanism, not a guarantee that a particular vocal editor accepts it. [Qt MIME data](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QMimeData.html), [Qt drag-and-drop](https://doc.qt.io/qtforpython-6/overviews/qtgui-dnd.html)

## Additions Beyond Review A

The revised plan also specifies interrupted-import recovery, dependency-preserving copies and their limits, same-file filter semantics, mutable file versus saved-version behavior, library removal versus physical deletion, database and vault backup/restore, worker/database ownership, migration safety, packaged-build validation, and measurable release gates.

These address gaps from the earlier review and make the proposal actionable. Performance budgets and format/platform support remain targets until measured; the plan does not present architectural choices as proof of working capabilities.

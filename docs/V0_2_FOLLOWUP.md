# v0.2 correctness and UI follow-up

## Implementation plan

1. Preserve explicit metadata choices during enrichment and grouping. Reject
   incompatible song identities before grouping, and preserve incoming metadata.
2. Use the FTS5 index for retrieval, retain independent fuzzy recall and short CJK
   matching, maintain indexes after edits, and preserve relevance in the table.
3. Correct VSQ3/VSQ4 schema and controller extraction using a pinned UtaFormatix
   reference. Retain bounded, read-only parsing and add realistic regressions.
4. Run enrichment work asynchronously, provide a forced refresh with field review,
   and keep cancellation and failure independent of local catalogue operations.
5. Redesign the inspector: synchronized version/file selection, concise summaries,
   expandable details, wrapping notes/terms, persistent actions, readable labels,
   unsaved-edit protection, visible match evidence, and persistent panel settings.
6. Run regressions, inspect representative layouts, record evidence and limitations,
   update release documentation, and commit the completed changes.

## Reference and scope

UtaFormatix3 reference commit: `f3c83354f57894492410bbc5ba03f7e97169c92c`.
Local reference: `D:\IdeaProjects\utaformatix3`. No runtime dependency is added.
Future adapters may reference its readers, but additional advertised formats need
their own fixtures and capability matrix. This follow-up targets UST and VSQX.

The user confirmed native dark-mode and real editor dragging work in their tested
environment. Exact OS/editor versions and formats were not supplied; this is
user-reported validation, not a universal compatibility claim.

## Results

Completed on 2026-09-30.

### v0.2 correctness

- Enrichment no longer clears a manual name override when the name field is
  unchecked. Dismissed aliases remain dismissed after enrichment or refresh.
  Applying a remote name changes the selected project, preserving other project
  names associated with the song.
- Grouping preserves incoming song names, aliases, credits, links, descriptions,
  and compatible overrides. Conflicting song identities or overrides reject the
  operation atomically. Grouping destinations include the whole library even
  when the visible table is filtered.
- Search now queries FTS5 instead of scanning every hydrated project. Literal
  quoting protects punctuation, short queries retain substring matching, and
  fuzzy recall independently considers the complete normalized vocabulary.
  SQL limits metadata hydration to candidates. Database migration 4 tracks dirty
  search data; edits and restores invalidate derived results. The UI presents
  strict results first, then fuzzy results, preserving relevance by default.
- VSQ3 field names normalize to the VSQ4 representation, including voice/style
  fields. Both schema families recognize actual pitch/dynamics controllers.
  XML construction enforces nesting and note/controller bounds and rejects
  DTD/entity declarations. Invalid/missing tempo remains unknown. The UST reader
  also handles legacy pitch-field spellings, empty lyrics, and zero-length
  vibrato without manufacturing evidence. Both parser versions are now 0.2.1.
- VocaDB search, preview, and apply run off the GUI thread. Refresh bypasses the
  remote cache and presents field choices before changing catalogue metadata.
  Errors leave local operations available; dismissed dialogs ignore late results.

### Inspector and interaction changes

- Metadata opens as a readable summary with an explicit edit action. Project
  credits and original-song credits have separate ownership labels.
- Versions & Files has a version selector, preferred marker, version-scoped file
  list, concise parsed summary, separate warnings, and a technical-details dialog.
  Selection and opening agree on the preferred version/default file; ambiguous
  fallback opens require a file choice.
- Paths elide in the middle, expose a tooltip, and offer Copy full path. Notes,
  terms, aliases, and descriptions wrap; notes/terms can open in a larger editor.
  Open and Reveal remain outside the scrolling content. Less common actions
  live in menus, and VocaDB actions fit narrow inspector widths.
- Save/Discard/Cancel protects drafts during selection, version, search, and
  close transitions. Late background results cannot replace unsaved drafts.
- Search evidence is visible beside results. View toggles hide filters/inspector;
  window geometry, splitter sizes, columns, tab, and panel visibility persist.
  Tests use isolated settings instead of changing the user's preferences.

### Validation

- **109 tests passed** across the full pytest suite. Regressions cover metadata
  preservation, grouping conflicts, actual FTS use and index invalidation,
  migration backup/restore, schema/controller parsing, resource rejection,
  preferred/default selection, unsaved drafts, asynchronous/cancelled enrichment,
  long inspector content, and saved layout preferences.
- Inspected offscreen screenshots at 1280×760 and 900×560. Both inspector tabs
  fit their viewport without horizontal overflow, while Open/Reveal remain
  visible. The project table may scroll horizontally at narrow window widths;
  panels can be hidden or resized to give it more space.
- All three supplied VSQ4 examples parsed successfully; extracted note counts
  matched the XML (242, 755, and 89). VSQ3 coverage uses a paired synthetic
  VSQ3/VSQ4 fixture, not a claim of testing every VOCALOID editor release.
- Changed Python files pass Ruff checks. The locked environment resolves, and
  source distribution/wheel builds succeed with license notices included.
- Native dark mode and real editor dragging are user-reported successes as
  described above. Offscreen tests do not establish native desktop integration
  or macOS/Linux compatibility. Those release gates remain open.

### Search measurements and limits

Reproduce with `uv run python scripts/benchmark_search.py`. The script creates
an isolated synthetic library containing 10,000 projects, 50,000 file records,
and 100,000 aliases. Raw results and environment details are in
[V0_2_SEARCH_BENCHMARK.json](V0_2_SEARCH_BENCHMARK.json).

| Query | First measured (ms) | Warm median (ms) | Warm maximum (ms) |
| --- | ---: | ---: | ---: |
| Exact name, strict retrieval | 68.56 | 32.74 | 33.11 |
| Typo, including fuzzy recall | 447.35 | 362.51 | 448.44 |
| Short CJK | 226.46 | 199.20 | 237.57 |
| Engine filter, no results | 124.27 | 128.69 | 150.37 |

Index rebuilding took 5.73 seconds and runs as background work in the UI. Each
query has one initial and five warm samples; the initial measurement follows
index construction and is **not** a cold-filesystem measurement. These small
synthetic samples do not establish release p95 targets or broad-filter rendering
performance. Short CJK sometimes exceeds 200 ms, and the full fuzzy pass is
slower than strict retrieval. The staged UI improves perceived responsiveness
without claiming that every performance target in the plan has been met.

### Licensing and future formats

Original project code is declared LGPL-3.0-or-later. The adapted schema mapping
retains Apache-2.0 identification, pinned attribution, a modifications notice,
and the upstream license text. Distribution includes LGPL/GPL texts and
`THIRD_PARTY_NOTICES.md`. Rewriting in Python does not remove attribution or
license obligations for adapted logic.

UtaFormatix3 can guide future readers, but this change does not add its other
formats or conversion pipeline. New format support still needs representative
fixtures, bounded parsing, read-only verification, and documented capabilities.

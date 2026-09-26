# v0.1 implementation and validation

This is the first runnable local-library implementation of `PLAN.md`. It is a
development build, not a signed platform release. Managed storage, ZIP extraction,
VocaDB, fuzzy search, and grouping controls remain in their planned later milestones.

## Implemented

- PySide6 three-panel library with background import/search/refresh, editable project
  names, descriptions, song aliases, project credits, tags, and workflow labels.
- SQLite schema, stable IDs, default versions/files, ownership constraints,
  serialized service writes, and a desktop process lock per library.
- Read-only external indexing, duplicate-location handling, separate records for
  identical bytes at different locations, hash checks, and per-file import errors.
- SVP observations including tempo units/maps, tracks, voices, languages, lyric
  counts, and pitch/vibrato/dynamics evidence. Extracted values are separate from
  manual catalogue metadata. Detection is not a tuning-quality assessment.
- Unicode-normalized literal substring search, aliases and contributor aliases,
  matching-field evidence, engine/voice/language/tuning/health filters bound to the
  same file, and all/preferred-version scopes.
- Refresh, explicit relink, Open, Open with, Reveal, catalogue-only removal,
  consistent SQLite metadata backup/restore, and JSON metadata export.
- Parser-version cache invalidation, failed-parse retries, pre-migration backups,
  and validation of database identity, integrity, foreign keys, and ownership.

## Parser evidence

| Format | Fixture evidence | Handling |
| --- | --- | --- |
| SVP schema 113 | 1 supplied file; synthetic regression | UTF-8 JSON; tempo, tracks, voices, lyrics, tuning evidence |
| SVP schema 134 | 6 supplied files; synthetic regression | Same fields; trailing NUL padding accepted within a limit |
| SVP schema 153 | 2 supplied files; synthetic regression | Same fields; referenced groups and note system attributes |
| UST, VSQX, MIDI | 12 supplied files | Indexed as opaque files; metadata parsing unsupported |
| USTX, VPR, CCS, PPSF | Opaque registration path | No metadata support advertised |

The editor builds that created the supplied samples are unknown. Schema coverage
does not claim support for every variant of an editor release. Dependencies and
audio-reference resolution are not advertised as checked. Parser input size,
nesting, notes, tracks, groups, and curve values are bounded; a separate hard
wall-clock parsing deadline is still a release-hardening item.

The optional `example/` corpus contains user-supplied projects and is not included
in the implementation commit or distributable package. When it is present, tests
index all 21 project files, parse all nine SVPs, and compare source SHA-256 hashes
before and after. Synthetic regression tests run without that corpus.

## Verification and remaining release gates

Validation was performed on Windows with Python 3.13.5 and the dependency versions
recorded in `uv.lock` (PySide6 6.11.2). Tests cover metadata round trips, preservation
of alias metadata/contributor identities, atomic inspector saves, migration/restore
rejection, same-file filters, parser-version refresh, cancellation between files,
and a headless Qt window. Source and wheel builds are also checked.

Final local checks: **52 tests passed** (including the optional supplied corpus),
Ruff passed, and both the wheel and source distribution built successfully.

Still requiring release validation or further UI work:

- Native Windows/macOS installers, clean-machine startup, signing, real editor
  launch/drop compatibility, and macOS testing. A wheel is not an installer.
- The planned 10,000-project benchmark and query optimization. Current search uses
  batched SQLite reads and in-memory normalized filtering, without an arbitrary
  candidate cutoff. It does not yet use SQL filtering or FTS.
- Rich song/original-credit/link/terms editors, per-file manual technical overrides,
  alias language/script controls, and persistent layout/theme preferences. The
  schema reserves these concepts; schema support alone is not a finished UI.
- Multi-valued track language/voice search currently reads bounded observation
  details; full relational indexing and voice-alias management remain unfinished.
- File health describes the last explicit import/refresh. There is no filesystem
  watcher. Drive and access failures are distinct from missing files where the OS
  provides that evidence; removable-mount behavior still needs macOS validation.

Metadata backups do not include external project bytes. Refreshing an overwritten
project cannot recover its previous bytes. No physical deletion or managed copying
is performed by catalogue removal.

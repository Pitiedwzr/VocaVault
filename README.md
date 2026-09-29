# VocaVault

VocaVault is an offline-first desktop catalogue for virtual singer projects.
Version 0.2 indexes files in place, extracts metadata from Synthesizer V Studio,
UTAU, and VOCALOID projects, and provides local search and optional VocaDB
enrichment without modifying source files.

## Development

This project requires Python 3.13 and uses `uv` for a reproducible environment.

```powershell
uv sync --extra dev
uv run pytest
uv run vocavault
```

The live library database is stored in the operating system's application-data
directory by default. Pass `--database PATH` to run against another library.
Project files remain at their original locations.

## v0.2 scope

- Index project files and folders in place with stable IDs and SHA-256 checks.
- Parse bounded SVP schema versions 113, 134, and 153, including the trailing
  NUL padding produced by some editor versions.
- Parse UST and VSQ3/VSQ4 XML metadata with bounded, read-only adapters.
- Catalogue USTX, VPR, CCS, PPSF, and MIDI files as unsupported opaque records
  until their adapters ship.
- Search normalized project names, aliases, credits, tags, filenames, and
  extracted voices; filter engine, voice, language, tuning signals, and health
  against the same file, with matching-field evidence.
- Use FTS5 retrieval, short-query matching, and typo-tolerant search.
- Group project versions, choose preferred versions and default files, and open,
  reveal, or drag files into supported applications.
- Review optional VocaDB enrichment and refreshes while preserving manual choices.
- Browse concise inspector summaries, edit wrapping notes and terms, and retain
  window/panel preferences with unsaved-edit protection.
- Refresh changed or missing files, explicitly relink matching content, and
  back up, restore, or export library metadata.

Metadata backups and JSON exports contain catalogue data and external paths.
They do not include the project files themselves.

This is a runnable development build. See
[v0.2 follow-up and validation notes](docs/V0_2_FOLLOWUP.md) for the tested
scope, benchmark results, and remaining platform-release gates. Health reflects the last
import or refresh; refresh after saving a file in an external editor.

## License

VocaVault's original code is licensed under **LGPL-3.0-or-later**. See
[LICENSE](LICENSE) and the accompanying [GPL terms](licenses/GPL-3.0.txt).
The adapted UtaFormatix3 schema mapping retains its Apache-2.0 terms and
attribution; see [third-party notices](THIRD_PARTY_NOTICES.md). UtaFormatix3 is
a source reference, not a runtime dependency.

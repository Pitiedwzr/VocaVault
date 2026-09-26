# VocaVault

VocaVault is an offline-first desktop catalogue for virtual singer projects.
Version 0.1 indexes files in place, extracts metadata from Synthesizer V Studio
projects, keeps unsupported formats cataloguable, and provides normalized local
search without modifying source files.

## Development

This project requires Python 3.13 and uses `uv` for a reproducible environment.

```powershell
uv sync --extra dev
uv run pytest
uv run vocavault
```

The live library database is stored in the operating system's application-data
directory by default. Pass `--database PATH` to run against another library.
Project files always remain at their original locations in v0.1.

## v0.1 scope

- Index project files and folders in place with stable IDs and SHA-256 checks.
- Parse bounded SVP schema versions 113, 134, and 153, including the trailing
  NUL padding produced by some editor versions.
- Catalogue UST, VSQX, USTX, VPR, CCS, PPSF, and MIDI files as unsupported
  opaque records until their adapters ship.
- Search normalized project names, aliases, credits, tags, filenames, and
  extracted voices; filter engine, voice, language, tuning signals, and health
  against the same file, with matching-field evidence.
- Refresh changed or missing files, explicitly relink matching content, and
  back up, restore, or export library metadata.

Metadata backups and JSON exports contain catalogue data and external paths.
They do not include the project files themselves.

This is a runnable development build. See
[implementation and validation notes](docs/V0_1_IMPLEMENTATION.md) for the tested
format matrix and remaining platform-release gates. Health reflects the last
import or refresh; refresh after saving a file in an external editor.

The optional user-supplied `example/` corpus is tested when present and is not
distributed. Synthetic regression tests run on a clean checkout.

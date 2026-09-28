from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from vocavault.library import PROJECT_EXTENSIONS, LibraryService


def test_example_corpus_indexes_and_preserves_all_sources(tmp_path: Path) -> None:
    example_root = Path(__file__).parents[1] / "example"
    if not example_root.is_dir():
        pytest.skip("Optional user-supplied example corpus is not present")
    candidates = sorted(
        path
        for path in example_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in PROJECT_EXTENSIONS
    )
    hashes = {path: hashlib.sha256(path.read_bytes()).digest() for path in candidates}
    library = LibraryService(tmp_path / "corpus.sqlite3")

    imported = library.import_paths([example_root])
    projects = library.list_projects()

    assert len(imported) == len(candidates) == len(projects)
    files = [file for project in projects for file in project["files"]]
    svp_files = [f for f in files if f["format_name"] == "svp"]
    ust_files = [f for f in files if f["format_name"] == "ust"]
    vsqx_files = [f for f in files if f["format_name"] == "vsqx"]

    assert len(svp_files) == 9
    assert len(ust_files) == 7
    assert len(vsqx_files) == 3

    supported_files = svp_files + ust_files + vsqx_files
    assert all(f["parse_status"] in {"parsed", "partial"} for f in supported_files)
    assert all(
        hashlib.sha256(path.read_bytes()).digest() == digest
        for path, digest in hashes.items()
    )

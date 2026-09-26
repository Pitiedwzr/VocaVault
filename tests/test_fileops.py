from __future__ import annotations

from pathlib import Path

from vocavault.fileops import fingerprint


def test_fingerprint_is_stable_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "声.svp"
    source.write_bytes(b'{"version": 1}')
    before = source.read_bytes()

    first = fingerprint(source)
    second = fingerprint(source)

    assert first == second
    assert first.size == len(before)
    assert len(first.sha256) == 64
    assert source.read_bytes() == before

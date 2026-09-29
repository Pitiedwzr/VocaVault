"""Reproducible synthetic search benchmark; never reads a user's library."""

import argparse
import hashlib
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from vocavault.library import LibraryService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=int, default=10_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vocavault-benchmark-") as directory:
        lib = LibraryService(Path(directory) / "benchmark.sqlite3")
        with lib.database.connection() as c:
            c.execute("BEGIN")
            for i in range(args.projects):
                digest = hashlib.sha256(str(i).encode()).hexdigest()[:20]
                c.execute("INSERT INTO songs(id) VALUES (?)", (f"s{i}",))
                c.execute(
                    "INSERT INTO projects(id,song_id,name,normalized_name) VALUES (?,?,?,?)",
                    (f"p{i}", f"s{i}", "Project " + digest, "project " + digest),
                )
                c.execute(
                    "INSERT INTO versions(id,project_id,label,normalized_label) VALUES (?,?,?,?)",
                    (f"v{i}", f"p{i}", "Default", "default"),
                )
                for j in range(5):
                    c.execute(
                        "INSERT INTO files(id,version_id,locator,detected_format,is_present) VALUES (?,?,?,'svp',1)",
                        (f"f{i}-{j}", f"v{i}", f"/synthetic/{digest}/{j}.svp"),
                    )
                c.execute(
                    "UPDATE versions SET default_file_id=? WHERE id=?",
                    (f"f{i}-0", f"v{i}"),
                )
                c.execute(
                    "UPDATE projects SET preferred_version_id=? WHERE id=?",
                    (f"v{i}", f"p{i}"),
                )
                for j in range(10):
                    alias = hashlib.sha256(f"{i}-{j}".encode()).hexdigest()[:24]
                    if i == j == 0:
                        alias = "ghost rule"
                    elif i == 0 and j == 1:
                        alias = "ゴーゴー幽霊船"
                    c.execute(
                        "INSERT INTO song_names(id,song_id,text,normalized_text) VALUES (?,?,?,?)",
                        (f"a{i}-{j}", f"s{i}", alias, alias),
                    )
            c.commit()
        started = time.perf_counter()
        lib.rebuild_search_index()
        rebuild_seconds = time.perf_counter() - started
        measurements = {}
        for name, params in [
            ("exact", {"query": "Ghost Rule", "include_fuzzy": False}),
            ("typo", {"query": "gosht rul"}),
            ("short_cjk", {"query": "幽霊"}),
            ("filter", {"engine": "ust"}),
        ]:
            samples = []
            for _ in range(6):
                started = time.perf_counter()
                results = lib.list_projects(**params)
                samples.append((time.perf_counter() - started) * 1000)
            measurements[name] = {
                "first_ms": round(samples[0], 2),
                "warm_median_ms": round(statistics.median(samples[1:]), 2),
                "warm_max_ms": round(max(samples[1:]), 2),
                "results": len(results),
            }
        report = {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "projects": args.projects,
            "files": args.projects * 5,
            "aliases": args.projects * 10,
            "rebuild_seconds": round(rebuild_seconds, 2),
            "measurements": measurements,
            "note": "First query follows index rebuild; this is not a cold filesystem-cache measurement. Five warm samples; max is reported, not a statistically robust p95.",
        }
        text = json.dumps(report, indent=2, ensure_ascii=False)
        print(text)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

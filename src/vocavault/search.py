"""Literal FTS retrieval with independent, untruncated typo candidates."""

import sqlite3

from rapidfuzz import fuzz, process


def load_vocabulary(connection: sqlite3.Connection) -> dict[str, set[str]]:
    vocabulary: dict[str, set[str]] = {}
    for project_id, text in connection.execute(
        "SELECT project_id, normalized_text FROM search_fts"
    ):
        if text:
            vocabulary.setdefault(text, set()).add(project_id)
    return vocabulary


def candidate_projects(
    connection: sqlite3.Connection,
    query: str,
    vocabulary: dict[str, set[str]],
    *,
    include_fuzzy: bool = True,
) -> set[str]:
    if len(query) >= 3:
        # Quoted FTS phrases escape syntax as well as SQL parameters.
        expression = '"' + query.replace('"', '""') + '"'
        rows = connection.execute(
            "SELECT project_id FROM search_fts WHERE normalized_text MATCH ?",
            (expression,),
        )
    else:
        rows = connection.execute(
            "SELECT project_id FROM search_fts WHERE instr(normalized_text, ?) > 0",
            (query,),
        )
    result = {row[0] for row in rows}
    if len(query) < 3 or not include_fuzzy:
        return result
    # Retrieve against the complete vocabulary, independent of strict hits.
    # File-level filters are applied after this superset is hydrated.
    for text, _score, _index in process.extract(
        query,
        list(vocabulary),
        scorer=fuzz.WRatio,
        score_cutoff=68,
        limit=None,
    ):
        result.update(vocabulary[text])
    return result

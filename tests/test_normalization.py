from vocavault.normalization import escape_like, normalize_search_text


def test_normalizes_width_case_whitespace_and_separators() -> None:
    assert normalize_search_text("  ＧＨＯＳＴ-rule\t") == "ghost rule"


def test_preserves_cjk_text() -> None:
    assert normalize_search_text("ゴーゴー幽霊船") == "ゴーゴー幽霊船"


def test_like_escaping() -> None:
    assert escape_like(r"10%_done\\x") == r"10\%\_done\\\\x"

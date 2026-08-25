from common.normalize import normalize_text


def test_trims_trailing_whitespace():
    assert normalize_text("DR   ") == "DR"


def test_collapses_internal_whitespace():
    assert normalize_text("WILMINGTON          ") == "WILMINGTON"
    assert normalize_text("MULTIPLE   SPACES  HERE") == "MULTIPLE SPACES HERE"


def test_none_and_blank_return_none():
    assert normalize_text(None) is None
    assert normalize_text("") is None
    assert normalize_text("   ") is None


def test_non_string_input():
    assert normalize_text(2600920086) == "2600920086"

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value):
    """Trim and collapse internal whitespace. Returns None for blank/missing input.

    The source org has a confirmed data-quality issue where text fields carry
    trailing/internal whitespace (e.g. "DR   ", "WILMINGTON          "), so every
    value pulled from Excel or from Salesforce must pass through this before
    being used as a lookup key or compared for equality.
    """
    if value is None:
        return None
    text = str(value)
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    return collapsed or None

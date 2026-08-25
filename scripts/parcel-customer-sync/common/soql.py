def escape_soql_string(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def chunk(values, chunk_size=200):
    values = list(values)
    for i in range(0, len(values), chunk_size):
        yield values[i : i + chunk_size]


def in_clause(values):
    escaped = [f"'{escape_soql_string(v)}'" for v in values]
    return "(" + ", ".join(escaped) + ")"

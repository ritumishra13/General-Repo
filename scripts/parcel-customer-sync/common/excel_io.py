import pandas as pd


def read_sheet(path, expected_columns):
    df = pd.read_excel(path, dtype=str)
    actual_columns = list(df.columns)
    missing = [c for c in expected_columns if c not in actual_columns]
    if missing:
        raise ValueError(
            f"{path} is missing expected column(s) {missing}. "
            f"Found columns: {actual_columns}. Refusing to guess at a shifted layout."
        )
    return df


def write_review_sheet(path, rows, columns):
    """rows: list of dicts. columns: ordered column list, must include 'Reason'."""
    df = pd.DataFrame(rows, columns=columns)
    df.to_excel(path, index=False)

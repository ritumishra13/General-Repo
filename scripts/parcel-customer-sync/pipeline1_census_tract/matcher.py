from common.normalize import normalize_text


def split_matched_unmatched(rows, parcel_by_name):
    """rows: list of dicts with 'Parcel' and 'CensusTract' (raw, unnormalized).
    parcel_by_name: dict of normalized Parcel Name -> Salesforce Id.

    Returns (matched, unmatched):
      matched: list of dicts {parcel_id, parcel_name, census_tract, source_row}
      unmatched: list of dicts {source_row, reason}
    """
    matched = []
    unmatched = []

    for row in rows:
        parcel_name = normalize_text(row.get("Parcel"))
        census_tract = normalize_text(row.get("CensusTract"))

        if parcel_name is None:
            unmatched.append({"source_row": row, "reason": "Blank Parcel value"})
            continue

        parcel_id = parcel_by_name.get(parcel_name)
        if parcel_id is None:
            unmatched.append({"source_row": row, "reason": f"Parcel not found in Salesforce: {parcel_name}"})
            continue

        matched.append(
            {
                "parcel_id": parcel_id,
                "parcel_name": parcel_name,
                "census_tract": census_tract,
                "source_row": row,
            }
        )

    return matched, unmatched

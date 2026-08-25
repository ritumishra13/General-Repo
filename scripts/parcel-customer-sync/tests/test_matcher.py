from pipeline1_census_tract.matcher import split_matched_unmatched


def test_matched_row():
    rows = [{"Parcel": "2600920086", "CensusTract": "2"}]
    parcel_by_name = {"2600920086": "a001"}
    matched, unmatched = split_matched_unmatched(rows, parcel_by_name)
    assert unmatched == []
    assert matched == [{"parcel_id": "a001", "parcel_name": "2600920086", "census_tract": "2", "source_row": rows[0]}]


def test_unmatched_row_parcel_not_found():
    rows = [{"Parcel": "9999999999", "CensusTract": "5"}]
    matched, unmatched = split_matched_unmatched(rows, parcel_by_name={})
    assert matched == []
    assert len(unmatched) == 1
    assert "not found" in unmatched[0]["reason"]


def test_blank_parcel_goes_to_unmatched():
    rows = [{"Parcel": None, "CensusTract": "5"}]
    matched, unmatched = split_matched_unmatched(rows, parcel_by_name={})
    assert matched == []
    assert unmatched[0]["reason"] == "Blank Parcel value"


def test_whitespace_in_sheet_value_still_matches():
    rows = [{"Parcel": "  2600920086   ", "CensusTract": "2"}]
    parcel_by_name = {"2600920086": "a001"}
    matched, unmatched = split_matched_unmatched(rows, parcel_by_name)
    assert len(matched) == 1
    assert unmatched == []

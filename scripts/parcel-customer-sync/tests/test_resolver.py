from pipeline2_owner_change.resolver import (
    ACTION_CREATE_ONLY,
    ACTION_NO_CHANGE,
    ACTION_OWNER_CHANGE,
    ACTION_REVIEW,
    dedup_most_recent,
    resolve_row,
)


def make_row(parcel="P1", cust_number="C1"):
    return {
        "Parcel": parcel,
        "prc_own_name": "JODKO PROPERTIES ONE LLC",
        "cust_number": cust_number,
        "cust_name": "JODKO PROPERTIES ONE LLC",
        "CensusTract": "2",
        "ZoneCode": "26R-1",
    }


def test_dedup_most_recent_keeps_first_seen_per_key():
    # Caller is responsible for pre-sorting by CreatedDate DESC, Id DESC;
    # dedup_most_recent just keeps the first record per key.
    records = [
        {"Name": "C1", "Id": "recent", "CreatedDate": "2026-02-01"},
        {"Name": "C1", "Id": "older", "CreatedDate": "2026-01-01"},
        {"Name": "C2", "Id": "only", "CreatedDate": "2025-01-01"},
    ]
    result = dedup_most_recent(records, key_field="Name")
    assert result["C1"]["Id"] == "recent"
    assert result["C2"]["Id"] == "only"


def test_review_when_parcel_not_found():
    row = make_row(parcel="MISSING")
    decision = resolve_row(row, parcel_by_name={}, customer_by_number={}, owner_junction_by_parcel_id={})
    assert decision["action"] == ACTION_REVIEW
    assert "Parcel not found" in decision["reason"]


def test_review_when_blank_parcel():
    row = make_row(parcel=None)
    decision = resolve_row(row, parcel_by_name={}, customer_by_number={}, owner_junction_by_parcel_id={})
    assert decision["action"] == ACTION_REVIEW
    assert decision["reason"] == "Blank Parcel value"


def test_review_when_customer_not_found_never_auto_creates():
    row = make_row(cust_number="MISSING")
    decision = resolve_row(
        row,
        parcel_by_name={"P1": "parcelId1"},
        customer_by_number={},
        owner_junction_by_parcel_id={},
    )
    assert decision["action"] == ACTION_REVIEW
    assert "Customer_Id__c not found" in decision["reason"]


def test_create_only_when_no_existing_owner_junction():
    row = make_row()
    decision = resolve_row(
        row,
        parcel_by_name={"P1": "parcelId1"},
        customer_by_number={"C1": {"id": "custId1", "account_id": "acctId1"}},
        owner_junction_by_parcel_id={},
    )
    assert decision["action"] == ACTION_CREATE_ONLY
    assert decision["parcel_id"] == "parcelId1"
    assert decision["customer"]["id"] == "custId1"


def test_no_change_when_same_customer_id():
    row = make_row()
    decision = resolve_row(
        row,
        parcel_by_name={"P1": "parcelId1"},
        customer_by_number={"C1": {"id": "custId1", "account_id": "acctId1"}},
        owner_junction_by_parcel_id={
            "parcelId1": {"id": "pcId1", "customer_id": "custId1", "account_id": "acctId1", "role_value": "Owner"}
        },
    )
    assert decision["action"] == ACTION_NO_CHANGE


def test_owner_change_when_different_customer_id_compared_by_id_not_name():
    row = make_row()
    decision = resolve_row(
        row,
        parcel_by_name={"P1": "parcelId1"},
        customer_by_number={"C1": {"id": "custIdNEW", "account_id": "acctIdNEW"}},
        owner_junction_by_parcel_id={
            "parcelId1": {"id": "pcIdOLD", "customer_id": "custIdOLD", "account_id": "acctIdOLD", "role_value": "Owner"}
        },
    )
    assert decision["action"] == ACTION_OWNER_CHANGE
    assert decision["new_role_value"] == "Previous Owner"
    assert decision["old_junction"]["id"] == "pcIdOLD"
    assert decision["customer"]["id"] == "custIdNEW"


def test_owner_change_preserves_other_roles_on_old_junction():
    row = make_row()
    decision = resolve_row(
        row,
        parcel_by_name={"P1": "parcelId1"},
        customer_by_number={"C1": {"id": "custIdNEW", "account_id": "acctIdNEW"}},
        owner_junction_by_parcel_id={
            "parcelId1": {
                "id": "pcIdOLD",
                "customer_id": "custIdOLD",
                "account_id": "acctIdOLD",
                "role_value": "Owner;Care Of",
            }
        },
    )
    assert decision["action"] == ACTION_OWNER_CHANGE
    assert decision["new_role_value"] == "Care Of;Previous Owner"

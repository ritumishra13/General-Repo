"""Pure decision logic for the owner-change pipeline. No I/O, no sf CLI calls --
everything here operates on plain Python dicts/lists so it's fully unit-testable
without a live org.
"""

from common.normalize import normalize_text
from common.picklist_utils import swap_role

ACTION_REVIEW = "REVIEW"
ACTION_CREATE_ONLY = "CREATE_ONLY"
ACTION_NO_CHANGE = "NO_CHANGE"
ACTION_OWNER_CHANGE = "OWNER_CHANGE"

ROLE_OWNER = "Owner"
ROLE_PREVIOUS_OWNER = "Previous Owner"


def dedup_most_recent(records, key_field, created_date_field="CreatedDate", id_field="Id"):
    """Given records already ordered by key ASC, created_date_field DESC, id_field DESC,
    keep only the first record seen per key (i.e. the most-recently-created, with Id
    DESC as a deterministic tie-break for same-instant duplicates).

    Returns dict: key -> record.
    """
    result = {}
    for record in records:
        key = record[key_field]
        if key not in result:
            result[key] = record
    return result


def resolve_row(row, parcel_by_name, customer_by_number, owner_junction_by_parcel_id):
    """
    row: dict from the Excel sheet (raw values).
    parcel_by_name: normalized Parcel Name -> parcel_id
    customer_by_number: normalized cust_number -> {"id": str, "account_id": str}
    owner_junction_by_parcel_id: parcel_id -> {"id": str, "customer_id": str, "account_id": str, "role_value": str}

    Returns a dict describing what to do with this row:
      {"action": ..., "reason": str|None, "parcel_id": str|None, "customer": dict|None,
       "old_junction": dict|None, "row": row}
    """
    parcel_name = normalize_text(row.get("Parcel"))
    cust_number = normalize_text(row.get("cust_number"))

    if parcel_name is None:
        return {"action": ACTION_REVIEW, "reason": "Blank Parcel value", "row": row}

    parcel_id = parcel_by_name.get(parcel_name)
    if parcel_id is None:
        return {"action": ACTION_REVIEW, "reason": f"Parcel not found in Salesforce: {parcel_name}", "row": row}

    if cust_number is None:
        return {"action": ACTION_REVIEW, "reason": "Blank cust_number value", "row": row, "parcel_id": parcel_id}

    customer = customer_by_number.get(cust_number)
    if customer is None:
        return {
            "action": ACTION_REVIEW,
            "reason": f"Customer_Id__c not found for cust_number: {cust_number}",
            "row": row,
            "parcel_id": parcel_id,
        }

    old_junction = owner_junction_by_parcel_id.get(parcel_id)

    if old_junction is None:
        return {
            "action": ACTION_CREATE_ONLY,
            "reason": None,
            "row": row,
            "parcel_id": parcel_id,
            "customer": customer,
        }

    if old_junction["customer_id"] == customer["id"]:
        return {
            "action": ACTION_NO_CHANGE,
            "reason": None,
            "row": row,
            "parcel_id": parcel_id,
            "customer": customer,
            "old_junction": old_junction,
        }

    new_role_value = swap_role(old_junction["role_value"], ROLE_OWNER, ROLE_PREVIOUS_OWNER)
    return {
        "action": ACTION_OWNER_CHANGE,
        "reason": None,
        "row": row,
        "parcel_id": parcel_id,
        "customer": customer,
        "old_junction": old_junction,
        "new_role_value": new_role_value,
    }


def resolve_all(rows, parcel_by_name, customer_by_number, owner_junction_by_parcel_id):
    return [resolve_row(row, parcel_by_name, customer_by_number, owner_junction_by_parcel_id) for row in rows]

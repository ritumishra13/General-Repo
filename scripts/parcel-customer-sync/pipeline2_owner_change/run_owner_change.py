#!/usr/bin/env python3
"""Pipeline 2: Owner-change file.

Usage:
  python -m pipeline2_owner_change.run_owner_change --input FILE --target-org ALIAS [--execute] [--confirm-production]
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import field_mapping, soql
from common.excel_io import read_sheet, write_review_sheet
from common.run_manifest import RunManifest
from common.sf_client import bulk_write, query
from pipeline2_owner_change.resolver import (
    ACTION_CREATE_ONLY,
    ACTION_NO_CHANGE,
    ACTION_OWNER_CHANGE,
    ACTION_REVIEW,
    dedup_most_recent,
    resolve_all,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "field_mapping.yaml"
DATA_DIR = ROOT / "data"

EXPECTED_COLUMNS = [
    "Parcel", "prc_own_name", "prc_own_address1", "prc_own_address2", "prc_own_city",
    "prc_own_state", "prc_own_zip", "cust_number", "cust_name", "cust_addr1", "cust_addr2",
    "cust_city", "cust_state", "cust_zip", "CensusTract", "ZoneCode",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Owner-change sync (Pipeline 2)")
    parser.add_argument("--input", required=True, help="Path to the owner_change_YYYY-MM.xlsx file")
    parser.add_argument("--target-org", required=True, help="sf CLI org alias (no default)")
    parser.add_argument("--execute", action="store_true", help="Write to Salesforce. Omit for dry-run.")
    parser.add_argument("--confirm-production", action="store_true", help="Required in addition to --execute for the production alias.")
    return parser.parse_args()


def confirm_production_write(target_org):
    print(f"You are about to WRITE to Salesforce org alias '{target_org}'.")
    typed = input(f"Type the org alias exactly to confirm ('{target_org}'): ")
    if typed != target_org:
        print("Confirmation did not match. Aborting.")
        sys.exit(1)


def fetch_parcel_by_name(parcel_names, parcel_cfg, target_org):
    parcel_by_name = {}
    for chunk in soql.chunk(parcel_names):
        records = query(
            f"SELECT Id, {parcel_cfg['name_field']} FROM {parcel_cfg['sobject']} "
            f"WHERE {parcel_cfg['name_field']} IN {soql.in_clause(chunk)}",
            target_org,
        )
        for rec in records:
            parcel_by_name[rec[parcel_cfg["name_field"]]] = rec["Id"]
    return parcel_by_name


def fetch_customer_by_number(cust_numbers, customer_cfg, target_org):
    all_records = []
    for chunk in soql.chunk(cust_numbers):
        records = query(
            f"SELECT Id, {customer_cfg['name_field']}, {customer_cfg['account_lookup_field']}, CreatedDate "
            f"FROM {customer_cfg['sobject']} WHERE {customer_cfg['name_field']} IN {soql.in_clause(chunk)} "
            f"ORDER BY {customer_cfg['name_field']} ASC, CreatedDate DESC, Id DESC",
            target_org,
        )
        all_records.extend(records)
    most_recent = dedup_most_recent(all_records, key_field=customer_cfg["name_field"])
    return {
        key: {"id": rec["Id"], "account_id": rec.get(customer_cfg["account_lookup_field"])}
        for key, rec in most_recent.items()
    }


def fetch_owner_junction_by_parcel_id(parcel_ids, pc_cfg, target_org):
    all_records = []
    for chunk in soql.chunk(parcel_ids):
        records = query(
            f"SELECT Id, {pc_cfg['parcel_lookup_field']}, {pc_cfg['customer_lookup_field']}, "
            f"{pc_cfg['account_lookup_field']}, {pc_cfg['association_role_field']}, CreatedDate "
            f"FROM {pc_cfg['sobject']} "
            f"WHERE {pc_cfg['parcel_lookup_field']} IN {soql.in_clause(chunk)} "
            f"AND {pc_cfg['association_role_field']} INCLUDES ('Owner') "
            f"ORDER BY {pc_cfg['parcel_lookup_field']} ASC, CreatedDate DESC, Id DESC",
            target_org,
        )
        all_records.extend(records)
    most_recent = dedup_most_recent(all_records, key_field=pc_cfg["parcel_lookup_field"])
    return {
        key: {
            "id": rec["Id"],
            "customer_id": rec.get(pc_cfg["customer_lookup_field"]),
            "account_id": rec.get(pc_cfg["account_lookup_field"]),
            "role_value": rec.get(pc_cfg["association_role_field"]),
        }
        for key, rec in most_recent.items()
    }


def main():
    args = parse_args()
    mapping = field_mapping.load(CONFIG_PATH)
    field_mapping.validate_against_org(mapping, args.target_org)

    parcel_cfg = mapping["parcel"]
    customer_cfg = mapping["customer"]
    pc_cfg = mapping["parcel_customer"]

    is_production_alias = "prod" in args.target_org.lower()
    if args.execute and is_production_alias and not args.confirm_production:
        print("Refusing to --execute against what looks like a production alias without --confirm-production.")
        sys.exit(1)
    if args.execute and is_production_alias:
        confirm_production_write(args.target_org)

    input_path = Path(args.input)
    df = read_sheet(input_path, expected_columns=EXPECTED_COLUMNS)
    rows = df.to_dict(orient="records")

    parcel_names = {str(r["Parcel"]).strip() for r in rows if r.get("Parcel")}
    cust_numbers = {str(r["cust_number"]).strip() for r in rows if r.get("cust_number")}

    parcel_by_name = fetch_parcel_by_name(parcel_names, parcel_cfg, args.target_org)
    customer_by_number = fetch_customer_by_number(cust_numbers, customer_cfg, args.target_org)
    owner_junction_by_parcel_id = fetch_owner_junction_by_parcel_id(
        list(parcel_by_name.values()), pc_cfg, args.target_org
    )

    decisions = resolve_all(rows, parcel_by_name, customer_by_number, owner_junction_by_parcel_id)

    review_rows = [d for d in decisions if d["action"] == ACTION_REVIEW]
    create_only_rows = [d for d in decisions if d["action"] == ACTION_CREATE_ONLY]
    owner_change_rows = [d for d in decisions if d["action"] == ACTION_OWNER_CHANGE]
    no_change_rows = [d for d in decisions if d["action"] == ACTION_NO_CHANGE]

    run_id = date.today().strftime("%Y-%m")
    manifest = RunManifest(pipeline="owner_change", target_org=args.target_org, dry_run=not args.execute, source_file=input_path)
    manifest.set_count("review", len(review_rows))
    manifest.set_count("create_only", len(create_only_rows))
    manifest.set_count("owner_change", len(owner_change_rows))
    manifest.set_count("no_change", len(no_change_rows))

    review_path = DATA_DIR / "review" / f"owner_change_review_{run_id}.xlsx"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    write_review_sheet(
        review_path,
        rows=[{"Parcel": d["row"].get("Parcel"), "cust_number": d["row"].get("cust_number"), "Reason": d["reason"]} for d in review_rows],
        columns=["Parcel", "cust_number", "Reason"],
    )
    print(f"Wrote review sheet: {review_path} ({len(review_rows)} rows needing human review)")

    new_owner_rows = create_only_rows + owner_change_rows

    if not args.execute:
        print(
            f"DRY RUN: {len(create_only_rows)} new Owner junction(s) to create (no prior owner), "
            f"{len(owner_change_rows)} owner change(s) (old junction -> Previous Owner + new Owner junction), "
            f"{len(no_change_rows)} unchanged. Re-run with --execute to apply."
        )
        manifest_path = DATA_DIR / "processed" / run_id / f"run_manifest_owner_change_{run_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest.write(manifest_path)
        print(f"Run manifest: {manifest_path}")
        return

    # ---- Execute, in the safe-failure order: insert new Owner junctions first ----
    insert_records = []
    for d in new_owner_rows:
        source_key = f"{d['row'].get('Parcel')}|{d['row'].get('cust_number')}"
        insert_records.append(
            {
                pc_cfg["parcel_lookup_field"]: d["parcel_id"],
                pc_cfg["customer_lookup_field"]: d["customer"]["id"],
                pc_cfg["account_lookup_field"]: d["customer"]["account_id"],
                pc_cfg["association_role_field"]: "Owner",
                "_source_row_key": source_key,
            }
        )

    insert_results = bulk_write("create", pc_cfg["sobject"], insert_records, args.target_org, tmp_dir=DATA_DIR / "tmp")

    succeeded_keys = set()
    for res, d in zip(insert_results, new_owner_rows):
        source_key = f"{d['row'].get('Parcel')}|{d['row'].get('cust_number')}"
        new_id = res.get("id") or res.get("sf__Id")
        success = res.get("success", bool(new_id))
        if success:
            succeeded_keys.add(source_key)
            manifest.record_write(pc_cfg["sobject"], new_id, "insert", source_key)
        else:
            manifest.data["written_ids"].append(
                {"sobject": pc_cfg["sobject"], "id": None, "operation": "insert_failed", "source_row_key": source_key}
            )

    # Only downgrade the old junction for rows whose new-owner insert actually succeeded.
    downgrade_records = []
    partial_failure_reviews = []
    for d in owner_change_rows:
        source_key = f"{d['row'].get('Parcel')}|{d['row'].get('cust_number')}"
        if source_key in succeeded_keys:
            downgrade_records.append(
                {
                    "Id": d["old_junction"]["id"],
                    pc_cfg["association_role_field"]: d["new_role_value"],
                    "_source_row_key": source_key,
                }
            )
        else:
            partial_failure_reviews.append(
                {
                    "Parcel": d["row"].get("Parcel"),
                    "cust_number": d["row"].get("cust_number"),
                    "Reason": "New Owner junction insert failed -- old owner junction left unchanged (not downgraded).",
                }
            )

    if downgrade_records:
        downgrade_results = bulk_write("update", pc_cfg["sobject"], downgrade_records, args.target_org, tmp_dir=DATA_DIR / "tmp")
        for res, rec in zip(downgrade_results, downgrade_records):
            manifest.record_write(pc_cfg["sobject"], rec["Id"], "update_role", rec["_source_row_key"])

    if partial_failure_reviews:
        # Append partial-failure rows onto the same review workbook.
        combined_review_rows = [
            {"Parcel": d["row"].get("Parcel"), "cust_number": d["row"].get("cust_number"), "Reason": d["reason"]}
            for d in review_rows
        ] + partial_failure_reviews
        write_review_sheet(review_path, rows=combined_review_rows, columns=["Parcel", "cust_number", "Reason"])
        print(f"WARNING: {len(partial_failure_reviews)} row(s) had a partial failure -- see review sheet.")

    print(
        f"Executed: {len(create_only_rows)} new Owner junction(s) created, "
        f"{len(owner_change_rows) - len(partial_failure_reviews)} owner change(s) completed, "
        f"{len(no_change_rows)} unchanged."
    )

    processed_dir = DATA_DIR / "processed" / run_id
    processed_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(input_path), processed_dir / input_path.name)

    manifest_path = processed_dir / f"run_manifest_owner_change_{run_id}.json"
    manifest.write(manifest_path)
    print(f"Run manifest: {manifest_path}")


if __name__ == "__main__":
    main()

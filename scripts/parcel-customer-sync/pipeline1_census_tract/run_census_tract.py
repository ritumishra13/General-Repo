#!/usr/bin/env python3
"""Pipeline 1: Census Tract file.

Usage:
  python -m pipeline1_census_tract.run_census_tract --input FILE --target-org ALIAS [--execute] [--confirm-production]
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
from pipeline1_census_tract.matcher import split_matched_unmatched

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "field_mapping.yaml"
DATA_DIR = ROOT / "data"


def parse_args():
    parser = argparse.ArgumentParser(description="Census Tract sync (Pipeline 1)")
    parser.add_argument("--input", required=True, help="Path to the census_tract_YYYY-MM.xlsx file")
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


def main():
    args = parse_args()
    mapping = field_mapping.load(CONFIG_PATH)
    field_mapping.validate_against_org(mapping, args.target_org)

    parcel_cfg = mapping["parcel"]
    is_production_alias = "prod" in args.target_org.lower()
    if args.execute and is_production_alias and not args.confirm_production:
        print("Refusing to --execute against what looks like a production alias without --confirm-production.")
        sys.exit(1)
    if args.execute and is_production_alias:
        confirm_production_write(args.target_org)

    input_path = Path(args.input)
    df = read_sheet(input_path, expected_columns=["Parcel", "CensusTract"])
    rows = df.to_dict(orient="records")

    parcel_names = {str(r["Parcel"]).strip() for r in rows if r.get("Parcel")}
    parcel_by_name = {}
    for name_chunk in soql.chunk(parcel_names):
        records = query(
            f"SELECT Id, {parcel_cfg['name_field']} FROM {parcel_cfg['sobject']} "
            f"WHERE {parcel_cfg['name_field']} IN {soql.in_clause(name_chunk)}",
            args.target_org,
        )
        for rec in records:
            parcel_by_name[rec[parcel_cfg["name_field"]]] = rec["Id"]

    matched, unmatched = split_matched_unmatched(rows, parcel_by_name)

    run_id = date.today().strftime("%Y-%m")
    manifest = RunManifest(pipeline="census_tract", target_org=args.target_org, dry_run=not args.execute, source_file=input_path)
    manifest.set_count("matched", len(matched))
    manifest.set_count("unmatched", len(unmatched))

    review_path = DATA_DIR / "review" / f"census_tract_review_{run_id}.xlsx"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    write_review_sheet(
        review_path,
        rows=[{"Parcel": u["source_row"].get("Parcel"), "CensusTract": u["source_row"].get("CensusTract"), "Reason": u["reason"]} for u in unmatched],
        columns=["Parcel", "CensusTract", "Reason"],
    )
    print(f"Wrote review sheet: {review_path} ({len(unmatched)} unmatched rows)")

    if args.execute:
        bulk_records = [
            {
                "Id": m["parcel_id"],
                parcel_cfg["census_tract_field"]: m["census_tract"],
                "_source_row_key": m["parcel_name"],
            }
            for m in matched
        ]
        results = bulk_write("update", parcel_cfg["sobject"], bulk_records, args.target_org, tmp_dir=DATA_DIR / "tmp")
        for res in results:
            manifest.record_write(parcel_cfg["sobject"], res.get("id") or res.get("sf__Id"), "update", res.get("_source_row_key"))
        print(f"Executed update for {len(matched)} matched Parcel record(s).")

        processed_dir = DATA_DIR / "processed" / run_id
        processed_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(input_path), processed_dir / input_path.name)
    else:
        print(f"DRY RUN: {len(matched)} Parcel record(s) would be updated. Re-run with --execute to apply.")

    manifest_path = DATA_DIR / "processed" / run_id / f"run_manifest_census_tract_{run_id}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.write(manifest_path)
    print(f"Run manifest: {manifest_path}")


if __name__ == "__main__":
    main()

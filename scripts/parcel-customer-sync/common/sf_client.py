"""Thin wrapper over the Salesforce CLI (`sf`).

This is the only module in the codebase that shells out to `sf`. All calls use
subprocess.run with an argument list (shell=False) -- never a shell string --
because the multi-select picklist delimiter ';' is also a shell command
separator, so building a shell string from field values is a real quoting
hazard here, not a theoretical one.
"""

import csv
import json
import subprocess
import uuid
from pathlib import Path


class SfCliError(RuntimeError):
    def __init__(self, message, stdout="", stderr=""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _run(args):
    result = subprocess.run(args, shell=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SfCliError(
            f"sf CLI command failed: {' '.join(args)}",
            stdout=result.stdout,
            stderr=result.stderr,
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SfCliError(
            f"sf CLI returned non-JSON output for: {' '.join(args)}",
            stdout=result.stdout,
            stderr=result.stderr,
        ) from exc


def query(soql, target_org):
    payload = _run(["sf", "data", "query", "--query", soql, "--target-org", target_org, "--json"])
    if payload.get("status") != 0:
        raise SfCliError(f"SOQL query failed: {soql}", stderr=json.dumps(payload))
    return payload["result"]["records"]


def describe(sobject, target_org):
    payload = _run(["sf", "sobject", "describe", "--sobject", sobject, "--target-org", target_org, "--json"])
    if payload.get("status") != 0:
        raise SfCliError(f"Describe failed for {sobject}", stderr=json.dumps(payload))
    return payload["result"]


def create_record(sobject, values, target_org):
    args = ["sf", "data", "create", "record", "--sobject", sobject, "--target-org", target_org, "--json", "--values"]
    args.append(" ".join(f"{k}='{v}'" for k, v in values.items()))
    payload = _run(args)
    if payload.get("status") != 0:
        raise SfCliError(f"Create failed for {sobject}: {values}", stderr=json.dumps(payload))
    return payload["result"]["id"]


def update_record(sobject, record_id, values, target_org):
    args = [
        "sf", "data", "update", "record",
        "--sobject", sobject,
        "--record-id", record_id,
        "--target-org", target_org,
        "--json", "--values",
    ]
    args.append(" ".join(f"{k}='{v}'" for k, v in values.items()))
    payload = _run(args)
    if payload.get("status") != 0:
        raise SfCliError(f"Update failed for {sobject} {record_id}: {values}", stderr=json.dumps(payload))
    return payload["result"]


def bulk_write(operation, sobject, records, target_org, tmp_dir, id_field=None):
    """Bulk insert/update via a temp CSV.

    `records` is a list of dicts. Every dict must include a `_source_row_key`
    column so results can be traced back to the originating spreadsheet row.
    Returns a list of dicts: each input record plus `sf__Id`/`sf__Created` and
    `success`/`error` as reported by the CLI's bulk result.
    """
    if not records:
        return []

    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    csv_path = tmp_dir / f"bulk_{operation}_{sobject}_{uuid.uuid4().hex}.csv"

    fieldnames = sorted({key for record in records for key in record.keys()})
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    args = [
        "sf", "data", operation, "bulk",
        "--sobject", sobject,
        "--file", str(csv_path),
        "--target-org", target_org,
        "--wait", "10",
        "--json",
    ]
    if operation == "update" and id_field is None:
        pass  # bulk update expects an `Id` column in the CSV itself
    payload = _run(args)
    if payload.get("status") != 0:
        raise SfCliError(f"Bulk {operation} failed for {sobject}", stderr=json.dumps(payload))
    return payload["result"]

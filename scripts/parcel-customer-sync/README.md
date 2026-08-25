# Parcel/Customer Monthly Sync

Automates the monthly reconciliation that used to be done by hand in Excel:

- **Pipeline 1 (`pipeline1_census_tract`)** — reads a `Parcel, CensusTract` file,
  updates `Parcel__c.CensusTract` for matched parcels, and writes unmatched
  parcels to a review sheet.
- **Pipeline 2 (`pipeline2_owner_change`)** — reads the 16-column owner-change
  file, resolves each parcel's current Owner `Parcel_Customer__c` junction and
  the row's `Customer_Id__c` (deduping to the most-recently-created record when
  duplicates exist), and either creates a first Owner junction, leaves things
  alone if the owner is unchanged, or performs an owner change (old junction's
  `Association_Role__c` flips `Owner` → `Previous Owner`, preserving any other
  roles already on it, and a new `Owner` junction is created). Unmatched rows
  always go to a review sheet — **nothing is ever auto-created**.

Both pipelines are independent, manually-triggered, and default to a **dry
run** (query + compute + write review sheets only — no Salesforce writes)
unless you pass `--execute`.

See the plan this was built from for full design rationale and risk
discussion: it's summarized below; ask if you want the original writeup.

## One-time setup

### 1. Install the Salesforce CLI

```
npm install -g @salesforce/cli
sf --version
```

### 2. Authenticate the sandbox org (do this first)

```
sf org login web --alias pc-sync-sandbox --instance-url https://test.salesforce.com
```

Do **not** authenticate or run against a production alias until the
production-readiness bar below has been met. See `config/org_aliases.md`.

### 3. Phase 0 — confirm real field API names (blocking)

`config/field_mapping.yaml` ships with **placeholder field names** (clearly
marked). Before running anything for real, confirm the actual API names in
your org:

```
sf sobject describe --sobject Parcel__c         --target-org pc-sync-sandbox --json
sf sobject describe --sobject Customer_Id__c    --target-org pc-sync-sandbox --json
sf sobject describe --sobject Account            --target-org pc-sync-sandbox --json
sf sobject describe --sobject Parcel_Customer__c --target-org pc-sync-sandbox --json
```

Update `config/field_mapping.yaml` with the confirmed names, and confirm that
`'Owner'` and `'Previous Owner'` exist verbatim as picklist values on
`Association_Role__c`. Both entrypoints re-validate this file against a live
describe at the start of every run and will fail fast (not silently) if a
configured field no longer exists.

### 4. Python environment

```
pip install -r requirements.txt        # runtime deps: pandas, openpyxl, pyyaml
pip install -r requirements-dev.txt     # adds pytest, for running the test suite
```

## Running the unit tests

No Salesforce org is needed for this — pure logic only:

```
cd scripts/parcel-customer-sync
pytest
```

These must be green before running against any org.

## Running a pipeline

Drop the month's file into `data/inbox/`, then:

```
# Pipeline 1 — dry run (default)
python -m pipeline1_census_tract.run_census_tract \
  --input data/inbox/census_tract_2026-08.xlsx \
  --target-org pc-sync-sandbox

# Pipeline 2 — dry run
python -m pipeline2_owner_change.run_owner_change \
  --input data/inbox/owner_change_2026-08.xlsx \
  --target-org pc-sync-sandbox
```

Add `--execute` to actually write once you've reviewed the dry-run output and
the review sheet under `data/review/`. Against what looks like a production
alias (alias name contains "prod"), `--execute` additionally requires
`--confirm-production` plus typing the alias name back at an interactive
prompt.

On a successful `--execute` run, the source file moves to
`data/processed/YYYY-MM/` alongside a JSON run manifest recording every
Salesforce Id written, so there's an audit trail for any manual follow-up.

## Before running against production

Do not run this against production until:

1. At least 2 consecutive full monthly cycles have run cleanly in sandbox
   with zero unexplained discrepancies against manual reconciliation.
2. The idempotency check passes — running the exact same file twice does not
   create duplicate junctions.
3. You've spot-checked ~20 changed/created sandbox records directly in
   Salesforce, including the multi-select role string on downgraded
   junctions.
4. You've run a **production dry-run "shadow" pass** (`--target-org
   pc-sync-prod`, no `--execute`) and compared its proposed changes against
   what you'd have done manually that month.

Only then authenticate `pc-sync-prod` and run with `--execute
--confirm-production`.

## What this deliberately does NOT do (v1 scope)

- No scheduling/cron/CI — manual invocation only.
- No email/SFTP ingestion — you drop files into `data/inbox/` yourself.
- No auto-creation of `Account` or `Customer_Id__c` records, ever — unmatched
  rows always go to the review sheet.
- No merging of the two pipelines.
- No dependency on the pre-existing `ParcelCustomerSyncBatch.cls` /
  `ParcelCustomerSyncScheduler.cls` Apex classes or the older design doc in
  this repo (`SALESFORCE_PARCEL_CUSTOMER_SYNC_DESIGN.md`) — those were built
  for a different (and in places incorrect) version of this process and are
  left untouched.

## Layout

```
common/            shared: sf CLI wrapper, SOQL helpers, normalization,
                    multi-select picklist helpers, Excel I/O, run manifest
pipeline1_census_tract/   matcher.py (pure logic) + run_census_tract.py (entrypoint)
pipeline2_owner_change/   resolver.py (pure logic) + run_owner_change.py (entrypoint)
config/             field_mapping.yaml (confirm before use), org_aliases.md
tests/              unit tests, no org required
data/               inbox/ processed/ review/ tmp/ -- gitignored (contains PII)
```

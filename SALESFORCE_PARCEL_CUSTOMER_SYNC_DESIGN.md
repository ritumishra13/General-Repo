# Salesforce Parcel–Customer Monthly Sync — Solution Design

## 1. Overview & Goals

Every month-end, an external system sends a spreadsheet containing Parcel (property) details and the associated Customer (owner/rental) details. Today a Salesforce developer manually reconciles this file against Salesforce by:

1. Matching parcels and updating Census Tract.
2. Detecting ownership changes and re-qualifying the old owner's role to "Care Of" while creating a new "Owner" junction record.
3. Creating brand-new Customer/Account records for customers not yet in Salesforce (plus their junction).
4. Creating missing junction records for customers that already exist but aren't linked to the parcel yet.

**Goal:** keep this reconciliation on its existing tooling — **Salesforce CLI (`sf`) and MS Excel** — while removing the error-prone manual parts (whitespace bugs, eyeballing owner-name matches) and preserving the org's existing duplicate-Account handling convention (pick the most recently created Account on a name match). This is **not** a staging-object + Apex batch pipeline; that approach was proposed in an earlier draft of this document and was not agreed to.

This document is a **design only** — no Apex or metadata is created as part of this pass.

## 2. Current Data Model

| Object | Purpose |
|---|---|
| `Parcel__c` | Property detail (address, Census Tract, zoning, etc.) — custom object |
| `Customer_Id__c` | Unique customer identifier record |
| `Account` | Customer name + address (standard object; known duplicates exist) |
| `Parcel_Customer__c` (junction) | Lookups to `Parcel__c`, `Customer_Id__c`, `Account` + `Association_Role__c` (Owner / Rental / Care Of / …) |

Relationship: one `Parcel__c` can have many `Parcel_Customer__c` records over time (one per role/owner-history entry); one `Customer_Id__c`/`Account` pair can be linked to many parcels.

**The only unique/External ID field in this data model is `Parcel__c.Name`.** `Customer_Id__c.Name` is *not* unique or flagged as an External ID — a given customer can only be matched by querying `Customer_Id__c` where `Name = cust_number` and checking whether anything comes back, not via a CLI `upsert` keyed on it.

## 3. Source File Analysis

Sample file (`Sample_Data.xlsx`, sheet `Sheet1`) columns and their target mapping:

| Source column | Sample value | Maps to | Notes |
|---|---|---|---|
| `Parcel` | `2600920086` | `Parcel__c.Name` | Existing unique/External ID field — the only one in this model |
| `prc_loc_addr_no`, `prc_loc_street_dir`, `prc_loc_street_name`, `prc_loc_street_type`, `prc_loc_zip` | `412`, `None`, `HAWTHORNE`, `DR   `, `19802` | `Parcel__c` address fields | Note trailing spaces on street type (`'DR   '`) — confirms the whitespace issue the user described |
| `prc_own_name`, `prc_own_address1/2`, `prc_own_city/state/zip` | `JODKO PROPERTIES ONE LLC`, `405 MILTON DR`, …, `WILMINGTON          ` | Reference only / owner-of-record as reported by source | Used to sanity-check against `cust_*`, not written directly to Salesforce |
| `cust_number` | `284336` | `Customer_Id__c.Name` | Used to look up an existing `Customer_Id__c`; not an External ID, so matching is query-based, not `upsert`-based |
| `cust_name`, `cust_addr1/2`, `cust_city/state/zip` | `JODKO PROPERTIES ONE LLC`, `405 MILTON DR                           `, …, `WILMINGTON          ` | `Account.Name` (reached via `Customer_Id__r.Account.Name`) + address | Trailing spaces confirmed here too — must be trimmed before any lookup/dedup |
| `CensusTract` | `2` | `Parcel__c.Census_Track__c` | Direct update per Step 1 |
| `ZoneCode` | `26R-1` | `Parcel__c.Zone_Code__c` (proposed if it doesn't exist) | Not mentioned in the original 4 steps but present in every row — included for completeness |

There is no `status` column in the current source file. An older version of the feed had one (`A` = active parcel), but the client now only sends active parcels, so this column and any related filtering logic no longer apply.

All three sample rows have `prc_own_name == cust_name`, so the sample doesn't exercise an ownership-change or new-customer scenario — the logic below is designed from the written process description, not inferred from the sample.

## 4. Assumptions & Open Questions

1. **Owner-change detection key:** compare on `cust_number` (`Customer_Id__c.Name`) as the primary key against the customer currently linked via the "Owner" junction for that parcel; fall back to a trimmed `cust_name` match only if `cust_number` is blank.
2. **File layout:** designed for the current single flat sheet (one row = parcel + customer combined).
3. **`ZoneCode`:** assumed to map to a `Zone_Code__c` field on `Parcel__c`, proposed to be created if it doesn't exist.
4. **New parcels:** the original process describes only updating *existing* parcels ("identify the available parcels in the org"). This design assumes parcel numbers not found in Salesforce are **flagged for manual review**, not auto-created — confirm if new-parcel auto-creation is actually wanted.
5. **Account/Customer dedup rule:** when more than one `Account` shares the same trimmed `Name`, the most recently created one is treated as the match (existing org convention).

## 5. Monthly Workflow (Salesforce CLI + Excel)

No staging custom object and no Apex batch class are part of this process. Each monthly cycle is a sequence of `sf` CLI queries/loads driven from an Excel workbook that does the matching and classification work formulas can handle.

**Step A — Export current Salesforce state for the parcels in this month's file (`sf`):**
```
sf data query --query "SELECT Id, Name, Census_Track__c FROM Parcel__c WHERE Name IN ('<parcel1>','<parcel2>',...)" --result-format csv > parcels_current.csv

sf data query --query "SELECT Id, Name, Account__c, Account__r.Name FROM Customer_Id__c WHERE Name IN ('<cust1>','<cust2>',...)" --result-format csv > customers_current.csv

sf data query --query "SELECT Id, Parcel__c, Parcel__r.Name, Customer_Id__c, Customer_Id__r.Name, Account__c, Account__r.Name, Association_Role__c FROM Parcel_Customer__c WHERE Parcel__r.Name IN ('<parcel1>',...)" --result-format csv > junctions_current.csv
```

**Step B — Excel: merge and classify each source row.** Using `VLOOKUP`/`XLOOKUP` against the three exports above, add columns to the source sheet:
- `Parcel_Id` — from `parcels_current.csv`; blank means "parcel not in Salesforce" → route to a review tab.
- `Customer_Id` / `Account_Id` — from `customers_current.csv`; blank means a new `Account` + `Customer_Id__c` must be created.
- `Existing_Owner_Junction_Id` — from `junctions_current.csv`, matched on `Parcel + cust_number`.
- `Owner_Changed` — formula comparing trimmed `cust_name` to trimmed `prc_own_name` (case-insensitive).
- Trim every text column (`=TRIM(...)`) before any of the above lookups — this is what removes the whitespace bug in the source file.

**Step C — Build the load files (still in Excel, one CSV per DML operation) and run them with `sf`:**
1. `Parcel__c` update (Census Tract) — every matched row:
   `sf data update bulk --sobject Parcel__c --file parcel_updates.csv --external-id Name --wait 10`
2. New `Account` records (rows with no `Account_Id`) — insert, capture new Ids:
   `sf data create bulk --sobject Account --file new_accounts.csv --wait 10`
3. New `Customer_Id__c` records (rows with no `Customer_Id`, using the `Account_Id` from step 2 or the existing one) — insert:
   `sf data create bulk --sobject Customer_Id__c --file new_customers.csv --wait 10`
4. `Parcel_Customer__c` update to `Care Of` (rows where `Owner_Changed = TRUE` and `Existing_Owner_Junction_Id` is populated):
   `sf data update bulk --sobject Parcel_Customer__c --file careof_updates.csv --wait 10`
5. New `Parcel_Customer__c` junction with `Association_Role__c = 'Owner'` — for every row where `Owner_Changed = TRUE`, **and** for rows where `Owner_Changed = FALSE` but `Existing_Owner_Junction_Id` was blank (no link exists yet):
   `sf data create bulk --sobject Parcel_Customer__c --file new_owner_junctions.csv --wait 10`

Because `Customer_Id__c.Name` isn't an External ID, steps 2–5 rely on the Ids resolved in Excel during Step B, not on CLI `upsert`. Only the `Parcel__c` update in step 1 can use `Name` directly since it's the one genuine External ID here.

**Step D — Reconciliation review.** For rows where `Owner_Changed = FALSE` and `Existing_Owner_Junction_Id` is populated, add one more Excel check: does that junction's `Account__r.Name` (from `junctions_current.csv`) match `prc_own_name`? If not, flag the row on a "Review" tab — this is a data-quality signal, not something to auto-correct.

## 6. Error Handling & Monitoring

- Each `sf data create/update bulk` command writes a job result file with per-row success/failure — save these alongside the month's workbook as the audit trail.
- Rows that fail a bulk load (e.g. a picklist value rejected, a required field missing) get pasted back into a "Failed" tab in the same workbook for the next retry, rather than silently dropped.
- The Excel workbook itself *is* the audit record for a given month — keep one workbook per monthly cycle rather than overwriting.

## 7. Edge Cases

| Case | Handling |
|---|---|
| Same parcel appears more than once in the file (e.g., mid-month correction resent) | Process rows in file order; last row for a given `Parcel` wins. Flag if this happens for visibility. |
| Customer moves from "Care Of" back to "Owner" (reactivation) | Treated as a normal ownership change — old owner (if different) → Care Of, a fresh `Owner` junction is created per §5 Step C.5 rather than reusing the old one. |
| Blank/malformed `Parcel` or `cust_number` | Row routed to the review tab, excluded from all CLI loads for that cycle. |
| Multiple ownership changes for the same parcel within one file | Same as "appears more than once" — last row wins; only one Care-Of transition is recorded per run. |
| Parcel number not found in Salesforce | Row routed to the review tab per Assumption 4 — not auto-created. |

## 8. Rollout Recommendation

1. **Sandbox first:** point the CLI at the sandbox (`sf org login web --instance-url https://test.salesforce.com`, matching `sfdx-project.json`'s `sfdcLoginUrl`) and run a full historical monthly file (not just the 3-row sample) through §5 to validate the Account dedup rule and owner-change logic against real duplicate data.
2. **Production go-live:** once a couple of sandbox cycles run clean, repeat the same CLI + Excel workflow against production.

## Verification (of this document)

- Every column present in the actual uploaded sample file is accounted for in §3's mapping table, using the corrected object/field names (`Parcel__c`, `Customer_Id__c.Name`, `Account.Name` via `Customer_Id__r`, `Parcel__c.Census_Track__c`).
- The `status` column and all logic tied to it has been removed — the client now sends only active parcels.
- The staging-object + Apex-batch approach has been removed; the workflow described in §5–§8 uses only Salesforce CLI and Excel.
- `Parcel__c.Name` is called out as the only existing unique/External ID field; `Customer_Id__c.Name` is explicitly not treated as one.

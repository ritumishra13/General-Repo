# Salesforce Parcel–Customer Monthly Sync — Solution Design

## 1. Overview & Goals

Every month-end, an external system sends a spreadsheet containing Parcel (property) details and the associated Customer (owner/rental) details. Today a Salesforce developer manually reconciles this file against Salesforce by:

1. Matching parcels and updating Census Tract.
2. Detecting ownership changes and re-qualifying the old owner's role to "Care Of" while creating a new "Owner" junction record.
3. Creating brand-new Customer/Account records for customers not yet in Salesforce (plus their junction).
4. Creating missing junction records for customers that already exist but aren't linked to the parcel yet.

**Goal:** replace this manual reconciliation with a staging-object + Apex batch pipeline that can run fully or partially automated each month, while preserving the org's existing duplicate-Account handling convention (pick the most recently created Account on a name match).

This document is a **design only** — no Apex or metadata is created as part of this pass.

## 2. Current Data Model

| Object | Purpose |
|---|---|
| `Parcel` | Property detail (address, Census Tract, zoning, etc.) |
| `Customer_Id__c` | Unique customer identifier record |
| `Account` | Customer name + address (standard object; known duplicates exist) |
| `Parcel_Customer__c` (junction) | Lookups to `Parcel`, `Customer_Id__c`, `Account` + `Association_Role__c` (Owner / Rental / Care Of / …) |

Relationship: one `Parcel` can have many `Parcel_Customer__c` records over time (one per role/owner-history entry); one `Customer_Id__c`/`Account` pair can be linked to many parcels.

## 3. Source File Analysis

Sample file (`Sample_Data.xlsx`, sheet `Sheet1`) columns and their target mapping:

| Source column | Sample value | Maps to | Notes |
|---|---|---|---|
| `Parcel` | `2600920086` | `Parcel.Parcel_Number__c` (proposed External ID) | Unique parcel key from the source system |
| `prc_loc_addr_no`, `prc_loc_street_dir`, `prc_loc_street_name`, `prc_loc_street_type`, `prc_loc_zip` | `412`, `None`, `HAWTHORNE`, `DR   `, `19802` | `Parcel` address fields | Note trailing spaces on street type (`'DR   '`) — confirms the whitespace issue the user described |
| `prc_own_name`, `prc_own_address1/2`, `prc_own_city/state/zip` | `JODKO PROPERTIES ONE LLC`, `405 MILTON DR`, …, `WILMINGTON          ` | Reference only / owner-of-record as reported by source | Used to sanity-check against `cust_*`, not written directly to Salesforce |
| `cust_number` | `284336` | `Customer_Id__c.Customer_Number__c` (proposed External ID) | Primary key for owner-change detection |
| `cust_name`, `cust_addr1/2`, `cust_city/state/zip` | `JODKO PROPERTIES ONE LLC`, `405 MILTON DR                           `, …, `WILMINGTON          ` | `Account` Name + address | Trailing spaces confirmed here too — must be trimmed before any lookup/dedup |
| `CensusTract` | `2` | `Parcel.CensusTract__c` | Direct update per Step 1 |
| `ZoneCode` | `26R-1` | `Parcel.Zone_Code__c` (proposed if it doesn't exist) | Not mentioned in the original 4 steps but present in every row — included for completeness |
| `status` | `A` | Row-validity flag | Governs whether a row is processed or routed to review (see Assumptions) |

All three sample rows have `status = 'A'` and `prc_own_name == cust_name`, so the sample doesn't exercise an ownership-change or new-customer scenario — the logic below is designed from the written process description, not inferred from the sample.

## 4. Assumptions & Open Questions

I was unable to get these confirmed interactively (the clarifying-question tool failed on a transient connection error), so they're called out explicitly here — please correct any of these before implementation begins:

1. **Owner-change detection key:** compare on `cust_number` (→ `Customer_Number__c`) as the primary key against the customer currently linked via the "Owner" junction for that parcel; fall back to a trimmed `cust_name` match only if `cust_number` is blank.
2. **File layout:** designed for the current single flat sheet (one row = parcel + customer combined). The staging schema below is generic enough to also accept a normalized multi-sheet feed (separate Parcel / Customer / Role sheets) later — worth pursuing since your provider is open to splitting the file, as it would remove redundancy and simplify matching, but not required for v1.
3. **`status` column:** assumed `'A'` = Active/valid and safe to process; any other value or blank is routed to an error/review queue rather than silently processed or silently dropped.
4. **`ZoneCode`:** assumed to map to a `Zone_Code__c` field on `Parcel`, proposed to be created if it doesn't exist.
5. **External ID fields:** assumed none currently exist for matching. This design proposes adding:
   - `Parcel.Parcel_Number__c` (External ID, Unique) — enables native `upsert` on Parcel.
   - `Customer_Id__c.Customer_Number__c` (External ID, Unique) — enables native `upsert` on Customer_Id__c.
   - `Account` has no native External ID story here because of the known duplicates; it's matched by trimmed Name + "most recently created wins," not upserted (see §7).
6. **New parcels:** the original process describes only updating *existing* parcels ("identify the available parcels in the org"). This design assumes parcels not found in Salesforce are **flagged for manual review**, not auto-created — confirm if new-parcel auto-creation is actually wanted.

## 5. Staging Layer Design

A custom object `Parcel_Customer_Staging__c` holds one record per source row before any target-object DML runs. This gives a safe place to validate, de-duplicate, and retry without touching live data on a bad run.

| Field | Type | Purpose |
|---|---|---|
| All source columns (`Parcel__c`, `Prc_Loc_*__c`, `Prc_Own_*__c`, `Cust_Number__c`, `Cust_*__c`, `CensusTract__c`, `ZoneCode__c`, `Status__c`) | Text/Number | Raw row data, loaded as-is |
| `Batch_Run_Id__c` | Text | Groups all rows from one monthly load together |
| `Processing_Status__c` | Picklist: `New`, `Processed`, `Error`, `Skipped` | Drives batch processing and reporting |
| `Error_Message__c` | Long Text | Captures why a row failed, for review |

**Normalization on load (not in Excel):** immediately after insert (via a `before insert` trigger or the loader step itself), every text field is trimmed and collapsed of redundant internal whitespace. This removes the org's current dependency on someone remembering to run Excel's `TRIM()` before upload.

## 6. Ingestion Options (Phased)

| Phase | Approach | Automation level | Effort |
|---|---|---|---|
| **Phase 1** (recommended start) | Data Loader or the standard Data Import Wizard loads the monthly file directly into `Parcel_Customer_Staging__c`; an Apex batch (scheduled or manually kicked off) does everything downstream. | Partial — file drop-off is manual, all reconciliation logic is automatic | Low — no new integration surface, uses existing tooling |
| **Phase 2** | Add an Apex **Inbound Email Handler** (source system emails the file monthly to a Salesforce-generated address) or a scheduled **Bulk API** pull from wherever the source system lands the file (SFTP/shared drive), parsing straight into staging. | Full | Medium — needs an email service or scheduled integration job, plus file-parsing (CSV is simplest; xlsx needs a parsing library or a request to have the source export CSV) |

Recommendation: ship Phase 1 first since the user already agreed to the staging + batch approach; it delivers ~90% of the time savings (all the error-prone manual matching) with minimal new infrastructure. Revisit Phase 2 once Phase 1 has run reliably for a couple of cycles.

## 7. Processing Logic (Apex Batch Design)

Because later steps depend on earlier ones (a junction can't be created until its Parcel and Customer/Account both exist), processing runs as a **chain of batches**, each kicked off from the previous one's `finish()`:

```
Schedulable (monthly, or manually invoked)
   └─▶ Batch A: Parcel upsert
          └─▶ Batch B: Customer_Id__c + Account resolve/create
                 └─▶ Batch C: Parcel_Customer__c reconciliation
                        └─▶ Finish: mark staging rows, email summary
```

### Batch A — Parcel upsert
- Query `Parcel_Customer_Staging__c` where `Processing_Status__c = 'New'` and `Status__c = 'A'`.
- `upsert` `Parcel` records on `Parcel_Number__c`, setting address fields, `CensusTract__c`, `Zone_Code__c`.
- Rows whose `Parcel_Number__c` doesn't match any existing Parcel **and** creation isn't in scope (per Assumption 6) are marked `Error__c` with a "Parcel not found — needs manual review" message and excluded from later batches.

### Batch B — Customer & Account resolve/create
- `upsert` `Customer_Id__c` on `Customer_Number__c`.
- For each: look up `Account` by trimmed `Name` (case-insensitive), `ORDER BY CreatedDate DESC LIMIT 1` — implementing the org's existing "pick the most recently created one" duplicate rule. If none found, create a new `Account`.
- Link the resolved `Account` to the `Customer_Id__c` record.

### Batch C — Parcel_Customer__c reconciliation (the core logic from steps 2–4)
For each staging row (now guaranteed to have a valid `Parcel` and `Customer_Id__c`/`Account`):

```
existingOwnerLink = query Parcel_Customer__c
                     where Parcel__c = thisParcel
                       and Association_Role__c = 'Owner'

if existingOwnerLink == null:
    // Step 4 case: parcel has no owner link at all yet
    insert new Parcel_Customer__c (Parcel, Customer_Id__c, Account, Role = 'Owner')

else if existingOwnerLink.Customer_Id__c != thisRow.Customer_Number:
    // Step 2 case: ownership changed
    update existingOwnerLink.Association_Role__c = 'Care Of'
    insert new Parcel_Customer__c (Parcel, Customer_Id__c, Account, Role = 'Owner')

else:
    // Owner unchanged — check step 3/4: does a junction already exist
    // for this exact parcel+customer combination at all?
    if no Parcel_Customer__c exists for (Parcel, Customer_Id__c):
        insert new Parcel_Customer__c (Role = 'Owner')
    // else: already correctly linked, nothing to do
```

Worked example using the sample rows: all three rows have `prc_own_name == cust_name` and (in this sample) no pre-existing junction is assumed, so each would fall into the "no existing owner link → insert new Owner junction" branch on a first run.

### Finish step
- Mark each processed staging row `Processed`, or `Error` with `Error_Message__c` populated.
- Send a summary email (or post to a Slack/Chatter feed) with counts: parcels updated, junctions created, junctions re-qualified to Care Of, new Customers/Accounts created, rows skipped/errored.

## 8. Error Handling & Monitoring

- All DML uses `Database.upsert`/`Database.insert` with `allOrNone=false` so one bad row doesn't fail the whole batch.
- Per-row failures write back to the staging record's `Error_Message__c` / `Processing_Status__c = 'Error'` for later review — no failure is silent.
- The `Parcel_Customer_Staging__c` object doubles as an audit trail: every monthly run's raw input and outcome is queryable after the fact.
- A simple report/list view on staging filtered to `Processing_Status__c = 'Error'` gives an at-a-glance monthly exception queue.

## 9. Edge Cases

| Case | Handling |
|---|---|
| Same parcel appears more than once in the file (e.g., mid-month correction resent) | Process rows in file order; last row for a given `Parcel_Number__c` wins. Flag if this happens for visibility. |
| Customer moves from "Care Of" back to "Owner" (reactivation) | Since Batch C compares against the current `Owner`-role junction only, a returning owner is treated as a normal ownership change — old owner (if different) → Care Of, returning customer's existing junction record is updated back to `Owner` rather than creating a duplicate. |
| Blank/malformed `Parcel` or `cust_number` | Row marked `Error` in staging at load time, excluded from all batches, never silently dropped. |
| Multiple ownership changes for the same parcel within one file | Same as "appears more than once" — last row wins; only one Care-Of transition is recorded per run. |
| `status` column value other than `'A'` | Row marked `Skipped`, excluded from processing, visible in the exception report. |

## 10. Rollout Recommendation

1. **Sandbox first:** create the two proposed External ID fields, the staging object, and the three chained batch classes in a full sandbox; run against a full historical monthly file (not just the 3-row sample) to validate the Account dedup rule and owner-change logic against real duplicate data.
2. **Phase 1 go-live:** keep the manual Data Loader upload into staging, let the batch chain run automatically after; developer just watches the summary email and the error queue.
3. **Phase 2 (later):** automate ingestion itself (inbound email handler or scheduled pull) once Phase 1 has proven stable for 1–2 monthly cycles, removing the last manual step.

## Verification (of this document)

- Every column present in the actual uploaded sample file is accounted for in §3's mapping table.
- Every one of the 4 manually-performed steps in the original process maps to a specific batch/step in §7.
- Every place a decision was made without explicit user confirmation is called out in §4 rather than silently assumed.

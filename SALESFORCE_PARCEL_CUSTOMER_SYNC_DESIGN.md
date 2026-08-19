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

## 4. Assumptions & Open Questions — CONFIRMED (see also §11)

The following were confirmed with the user against the actual `NCC_Data_Upload_Guide.pdf` process description:

1. **Owner-change detection key:** compare on `cust_number` (→ `Customer_Number__c`) as the primary, stable external-ID key against the customer currently linked via the "Owner" `Parcel_Customer__c` junction for that parcel — **not** a same-row name comparison (see the bug called out in §11). Name comparison is fallback-only / low-confidence, never the primary signal.
2. **File layout:** designed for the current single flat sheet (one row = parcel + customer combined). The staging schema below is generic enough to also accept a normalized multi-sheet feed (separate Parcel / Customer / Role sheets) later — worth pursuing since your provider is open to splitting the file, as it would remove redundancy and simplify matching, but not required for v1.
3. **`status` column:** assumed `'A'` = Active/valid and safe to process; any other value or blank is routed to an error/review queue rather than silently processed or silently dropped.
4. **`ZoneCode`:** assumed to map to a `Zone_Code__c` field on `Parcel`, proposed to be created if it doesn't exist.
5. **External ID fields:** confirmed — add a stable external ID as the primary match key rather than relying on name/address fuzzy matching:
   - `Parcel.Parcel_Number__c` (External ID, Unique) — enables native lookup on Parcel (update-only; see point 6, no upsert/auto-create).
   - `Customer_Id__c.Customer_Number__c` (External ID, Unique) — enables native `upsert` on Customer_Id__c and is the primary owner-change match key.
   - `Account` has no native External ID story here because of the known duplicates; **a one-time cleanup/merge pass over existing duplicate Accounts is now in scope for this project** (see §12) so that, post-cleanup, new Accounts are resolved via the `Customer_Id__c → Account` relationship rather than by name-matching against a still-messy pool. Name + "most recently created wins" is retained only as the cleanup-pass heuristic and for genuinely first-time Account creation (see §7).
6. **New parcels:** confirmed — the original process only updates *existing* parcels. Parcels not found in Salesforce are **flagged for manual review**, never auto-created.
7. **Ingestion automation:** confirmed **in scope for this project**, not deferred to a later phase — see revised §6.

## 5. Staging Layer Design

A custom object `Parcel_Customer_Staging__c` holds one record per source row before any target-object DML runs. This gives a safe place to validate, de-duplicate, and retry without touching live data on a bad run.

| Field | Type | Purpose |
|---|---|---|
| All source columns (`Parcel__c`, `Prc_Loc_*__c`, `Prc_Own_*__c`, `Cust_Number__c`, `Cust_*__c`, `CensusTract__c`, `ZoneCode__c`, `Status__c`) | Text/Number | Raw row data, loaded as-is |
| `Batch_Run_Id__c` | Text | Groups all rows from one monthly load together |
| `Processing_Status__c` | Picklist: `New`, `Processed`, `Error`, `Skipped` | Drives batch processing and reporting |
| `Error_Message__c` | Long Text | Captures why a row failed, for review |

**Normalization on load (not in Excel):** immediately after insert (via a `before insert` trigger or the loader step itself), every text field is trimmed and collapsed of redundant internal whitespace. This removes the org's current dependency on someone remembering to run Excel's `TRIM()` before upload.

## 6. Ingestion Options (Phased build, single project)

**Update:** the user has confirmed that fully automating file ingestion (not just downstream reconciliation) is in scope for this project — it will not be deferred indefinitely as a separate future initiative. However, the *build sequence* is still phased for risk-reduction reasons:

| Phase | Approach | Automation level | Effort |
|---|---|---|---|
| **Phase 1** (build & prove first) | Data Loader or the standard Data Import Wizard loads the monthly file directly into `Parcel_Customer_Staging__c`; the Apex batch chain (scheduled or manually kicked off) does everything downstream. | Partial — file drop-off is manual, all reconciliation logic is automatic | Low — no new integration surface, uses existing tooling |
| **Phase 2** (built in the same project, wired on after Phase 1 is proven) | Add an Apex **Inbound Email Handler** (source system emails the file monthly to a Salesforce-generated address) or a scheduled **Bulk API** pull from wherever the source system lands the file (SFTP/shared drive), parsing straight into staging, plus a volume-anomaly check (hold for manual approval if row count deviates sharply from the prior month) so a malformed or wrong file doesn't auto-process. | Full | Medium — needs an email service or scheduled integration job, plus file-parsing (CSV is simplest; xlsx needs a parsing library or a request to have the source export CSV) |

Recommendation: build and validate Phase 1 (reconciliation logic) against a full historical file with a human still uploading it, *then* wire in Phase 2 ingestion automation within the same overall project — a matching-logic bug is far easier to catch and fix while a human is still in the loop on the file drop-off step.

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

> **⚠ Known discrepancy — code vs. this design (must fix before build continues):** the pseudocode above is correct — it compares the incoming customer against the *currently stored Salesforce Owner* (`existingOwnerLink.Customer_Id__c`). The current `ParcelCustomerSyncBatch.cls` implementation does **not** do this; it instead sets `ownerChanged` by comparing two columns on the *same incoming row* (`Prc_Own_Name__c` vs `Cust_Name__c`), which is a same-row data-quality check, not owner-change detection against Salesforce state. This needs to be corrected in Batch C to match the pseudocode above — using `Customer_Number__c` (external ID) as the comparison key, not a name comparison — before this goes further than sandbox testing. Also verify `Census_Tract__c` (per the actual upload guide) vs. the code's `Census_Track__c` and the object's actual singular/plural name against the real org schema — see §11.

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
3. **Phase 2 (built in this same project, wired on once Phase 1 is stable for 1–2 monthly cycles):** automate ingestion itself (inbound email handler or scheduled pull), removing the last manual step.

## 11. Field/Object Naming — Verify Against Real Org Before Any Deploy

Cross-checking this design and the existing Apex against the actual `NCC_Data_Upload_Guide.pdf` process description surfaced naming drift that **must be resolved against the live org schema before any metadata deploy** (a wrong API name fails as a deploy error, not a runtime bug):

| Item | This doc / Apex uses | The actual upload guide's SOQL uses | Action |
|---|---|---|---|
| Census Tract field | `Census_Track__c` (Apex), `CensusTract__c` (this doc, §5/§7) | `Census_Tract__c` | Confirm the real field name in the org; standardize all references to it before build. |
| Junction object | `Parcel_Customer__c` (singular) | `Parcel_Customer__c` (singular) | Matches — but note the original task description referred to it as `Parcel_Customers__c` (plural); confirm which is correct in the org. |
| Customer object | `Customer_Id__c` / `Customer_ID__c` | `Customer_ID__c` | Case only — confirm exact casing (Salesforce API names are effectively case-preserving in metadata even though SOQL is case-insensitive). |

## 12. Duplicate Account Cleanup (One-Time Pass — Now In Scope)

Confirmed with the user: rather than only preventing *new* duplicates going forward, this project includes a **one-time cleanup/merge pass** over Accounts that already have duplicates in the org (created by past manual uploads), before automated reconciliation is turned on.

- **Discovery first:** run a duplicate-count report (trimmed Name + Billing address similarity) before committing to a cleanup timeline — actual volume is currently unknown and could swing the estimate below significantly.
- **Merge execution:** for each duplicate group, keep one canonical Account (default: most-recently-created, matching the org's existing informal convention) and merge/re-point any `Customer_Id__c` and `Parcel_Customer__c` records currently linked to the Accounts being retired.
- **Ambiguous cases** (e.g., genuinely distinct entities with similar names, such as "Smith Family Trust" vs. "Smith Family Trust II") are routed to manual review, never auto-merged on a fuzzy match alone.
- This pass is a **hard prerequisite** to building/testing Batch B's Account-resolution logic — testing dedup-dependent logic against a still-messy Account base produces misleading test results.

## 13. Effort Estimate (Phased)

| Phase | Work | Estimate |
|---|---|---|
| 1. Design finalize + org verification | Resolve the naming discrepancies in §11, confirm/create the two External ID fields, obtain a full historical monthly file (not the 3-row sample) | 2–3 days |
| 2. Duplicate Account cleanup (one-time, §12) | Discovery report, merge decisions, execute merges, re-point existing lookups | 3–5 days (depends on duplicate volume — unknown, see Risks) |
| 3. Sandbox build — staging object + Batch A/B/C | Create `Parcel_Customer_Staging__c`, build/fix the three chained batches (including the owner-change logic fix in §7's callout), build the exception list view/report | 5–7 days |
| 4. Ingestion automation (§6 Phase 2) | Inbound email handler or scheduled SFTP/Bulk API pull into staging, file-format validation, volume-anomaly hold | 3–5 days |
| 5. Testing | Full historical file run, plus constructed test rows for: genuine ownership change, same-owner data-quality mismatch, brand-new customer, existing-but-unlinked customer, parcel not in Salesforce, malformed row | 3–4 days |
| 6. Rollout | Production deploy, first live cycle run in parallel with a manual spot-check, monitor exception queue | 2–3 days + 1 monthly cycle of parallel-run monitoring |

**Total: roughly 4–5 weeks**, plus one full monthly cycle running in shadow/parallel mode before the manual process is retired. **Recommended sequencing:** build and prove Phases 1–3 (reconciliation logic) against a full historical file while a human still uploads it, before wiring in Phase 4 ingestion automation — a matching-logic bug is much easier to catch and fix while a human is still in the loop on file drop-off. Phase 2 (dedup cleanup) is a hard prerequisite to Phase 3, not parallelizable with it.

## 14. Concerns & Risks

- **Ownership change vs. data-quality mismatch:** fixed logic (§7 callout) compares the incoming customer's `Customer_Number__c` against the currently-stored Salesforce Owner — but this assumes the source system's `cust_number` is stable and never reissued for a different real-world owner. If it ever is, that would misfire as "no change" when one occurred. Recommend a plausibility check (flag if `Cust_Name__c` changes materially while `cust_number` stays the same, or vice versa) routed to manual review rather than auto-applied.
- **Duplicate Accounts:** cleanup volume (§12) is unknown until the discovery pass runs — the 3–5 day estimate could expand for widespread or genuinely-ambiguous duplication.
- **Records present in the file but missing from Salesforce, and vice versa:** parcels in the file with no SF match are flagged for review, never auto-created (matches the guide). SF parcels/customers absent from a given month's file are left untouched — this design never deactivates a parcel or downgrades a customer link solely because it's missing from one month's file. If a parcel is subdivided or retired at the county level, this design has no mechanism to detect or flag that — confirm this is acceptable.
- **Bad monthly file / rollback:** per-row DML with `allOrNone=false` means one bad row never blocks the batch; the staging object is the audit trail. A bad *whole file* (wrong format, wrong month, truncated) should be caught by the Phase 2 ingestion volume-anomaly check before it reaches the batches at all, holding for manual approval. All failures surface via per-row `Error_Message__c`, the exception list view, and the finish-step summary email — nothing fails silently.

## Verification (of this document)

- Every column present in the actual uploaded sample file is accounted for in §3's mapping table.
- Every one of the 4 manually-performed steps in the original process maps to a specific batch/step in §7.
- Every place a decision was made without explicit user confirmation is called out in §4 rather than silently assumed, and every naming/logic discrepancy found against the actual upload guide is called out in §7 and §11 rather than silently reconciled.

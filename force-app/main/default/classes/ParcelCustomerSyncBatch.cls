/**
 * Monthly Parcel/Customer reconciliation batch.
 *
 * Reconciles rows landed in Parcel_Customer_Staging__c (one row per line of the
 * monthly source spreadsheet) against Parcel__c, Customer_ID__c, Account and the
 * Parcel_Customer__c junction, per the four-step business process:
 *   1. Match Parcel by number, update Census_Track__c.
 *   2/3. If cust_name != prc_own_name (owner changed), flip the existing
 *        Parcel+Customer junction to 'Care Of' and always create a fresh
 *        junction with role 'Owner' (reusing an existing Customer_ID__c/Account
 *        when one already matches, per the org's dedupe rule).
 *   4. If cust_name == prc_own_name (no owner change) but no junction exists yet
 *      for this Parcel+Customer, create the missing 'Owner' junction the same
 *      way. If a junction already exists, flag it for review when its linked
 *      Account name doesn't match prc_own_name.
 *
 * Assumed Parcel_Customer_Staging__c fields (rename to match your org if different):
 *   Parcel__c, Prc_Own_Name__c, Cust_Number__c, Cust_Name__c, Cust_Addr1__c,
 *   Cust_Addr2__c, Cust_City__c, Cust_State__c, Cust_Zip__c, CensusTract__c,
 *   Processing_Status__c (picklist: New / Processed / Review / Error), Error_Message__c.
 */
public class ParcelCustomerSyncBatch implements Database.Batchable<SObject>, Database.Stateful {

    private static final String ROLE_OWNER = 'Owner';
    private static final String ROLE_CARE_OF = 'Care Of';

    private static final String STATUS_PROCESSED = 'Processed';
    private static final String STATUS_ERROR = 'Error';
    private static final String STATUS_REVIEW = 'Review';

    public Integer parcelsUpdatedCount = 0;
    public Integer accountsCreatedCount = 0;
    public Integer customersCreatedCount = 0;
    public Integer ownerJunctionsCreatedCount = 0;
    public Integer careOfUpdatesCount = 0;
    public Integer rowsFlaggedForReviewCount = 0;
    public Integer rowsErroredCount = 0;

    public Database.QueryLocator start(Database.BatchableContext bc) {
        return Database.getQueryLocator([
            SELECT Id, Parcel__c, Prc_Own_Name__c, Cust_Number__c, Cust_Name__c,
                   Cust_Addr1__c, Cust_Addr2__c, Cust_City__c, Cust_State__c, Cust_Zip__c,
                   CensusTract__c
            FROM Parcel_Customer_Staging__c
            WHERE Processing_Status__c = 'New'
        ]);
    }

    public void execute(Database.BatchableContext bc, List<Parcel_Customer_Staging__c> scope) {

        List<Parcel_Customer_Staging__c> stagingToClose = new List<Parcel_Customer_Staging__c>();

        // ---- STEP 1: match Parcel by number, update Census_Track__c ----
        Set<String> parcelNumbers = new Set<String>();
        for (Parcel_Customer_Staging__c s : scope) {
            if (String.isNotBlank(s.Parcel__c)) {
                parcelNumbers.add(s.Parcel__c.trim());
            }
        }

        Map<String, Parcel__c> parcelByNumber = new Map<String, Parcel__c>();
        for (Parcel__c p : [SELECT Id, Name, Census_Track__c FROM Parcel__c WHERE Name IN :parcelNumbers]) {
            parcelByNumber.put(p.Name, p);
        }

        Map<Id, Parcel__c> parcelsToUpdate = new Map<Id, Parcel__c>();
        List<RowContext> rows = new List<RowContext>();

        for (Parcel_Customer_Staging__c s : scope) {
            String parcelNo = String.isNotBlank(s.Parcel__c) ? s.Parcel__c.trim() : null;
            String custNo = String.isNotBlank(s.Cust_Number__c) ? s.Cust_Number__c.trim() : null;
            String custName = String.isNotBlank(s.Cust_Name__c) ? s.Cust_Name__c.trim() : null;

            if (parcelNo == null || custNo == null || custName == null) {
                s.Processing_Status__c = STATUS_ERROR;
                s.Error_Message__c = 'Missing required value in Parcel / cust_number / cust_name.';
                stagingToClose.add(s);
                rowsErroredCount++;
                continue;
            }

            Parcel__c parcel = parcelByNumber.get(parcelNo);
            if (parcel == null) {
                s.Processing_Status__c = STATUS_ERROR;
                s.Error_Message__c = 'Parcel not found in Salesforce: ' + parcelNo;
                stagingToClose.add(s);
                rowsErroredCount++;
                continue;
            }

            parcel.Census_Track__c = s.CensusTract__c;
            parcelsToUpdate.put(parcel.Id, parcel);

            String prcOwnName = String.isNotBlank(s.Prc_Own_Name__c) ? s.Prc_Own_Name__c.trim() : '';
            Boolean ownerChanged = !custName.equalsIgnoreCase(prcOwnName);

            rows.add(new RowContext(s, parcel, parcelNo, custNo, custName, ownerChanged));
        }

        if (!parcelsToUpdate.isEmpty()) {
            update parcelsToUpdate.values();
            parcelsUpdatedCount += parcelsToUpdate.size();
        }

        if (rows.isEmpty()) {
            if (!stagingToClose.isEmpty()) {
                update stagingToClose;
            }
            return;
        }

        // ---- shared lookups needed by steps 2-4 ----
        Set<String> custNumbers = new Set<String>();
        Set<String> custNames = new Set<String>();
        for (RowContext r : rows) {
            custNumbers.add(r.custNumber);
            custNames.add(r.custName);
        }

        Map<String, Customer_ID__c> customerByNumber = new Map<String, Customer_ID__c>();
        for (Customer_ID__c c : [SELECT Id, Name, Account__c FROM Customer_ID__c WHERE Name IN :custNumbers]) {
            customerByNumber.put(c.Name, c);
        }

        // Most-recently-created Account per Name - the org's existing duplicate-Account rule.
        Map<String, Account> accountByName = new Map<String, Account>();
        for (Account a : [SELECT Id, Name, CreatedDate FROM Account WHERE Name IN :custNames ORDER BY CreatedDate DESC]) {
            if (!accountByName.containsKey(a.Name)) {
                accountByName.put(a.Name, a);
            }
        }

        // Existing Parcel_Customer__c junctions, keyed by "Parcel number|Customer number".
        Map<String, Parcel_Customer__c> junctionByKey = new Map<String, Parcel_Customer__c>();
        for (Parcel_Customer__c pc : [
            SELECT Id, Parcel__r.Name, Customer_ID__r.Name, Account__r.Name, Association_Role__c
            FROM Parcel_Customer__c
            WHERE Parcel__r.Name IN :parcelNumbers AND Customer_ID__r.Name IN :custNumbers
        ]) {
            junctionByKey.put(pc.Parcel__r.Name + '|' + pc.Customer_ID__r.Name, pc);
        }

        // ---- STEP 2/3 & STEP 4 (not-found case): work out which rows need a new Account/Customer_ID__c ----
        Map<String, Account> newAccountByName = new Map<String, Account>();
        Map<String, Customer_ID__c> newCustomerByNumber = new Map<String, Customer_ID__c>();
        Map<String, String> pendingCustomerAccountName = new Map<String, String>();

        for (RowContext r : rows) {
            String key = r.parcelNumber + '|' + r.custNumber;
            r.existingJunction = junctionByKey.get(key);
            r.needsOwnerJunction = r.ownerChanged || r.existingJunction == null;

            if (!r.needsOwnerJunction) {
                continue; // STEP 4 "matched" case - handled later as a name-mismatch review check
            }
            if (customerByNumber.containsKey(r.custNumber) || newCustomerByNumber.containsKey(r.custNumber)) {
                continue; // Customer_ID__c already present (or already queued this chunk) - reuse it
            }

            if (!accountByName.containsKey(r.custName) && !newAccountByName.containsKey(r.custName)) {
                newAccountByName.put(r.custName, new Account(
                    Name = r.custName,
                    BillingStreet = trimJoin(r.stagingRow.Cust_Addr1__c, r.stagingRow.Cust_Addr2__c),
                    BillingCity = r.stagingRow.Cust_City__c,
                    BillingState = r.stagingRow.Cust_State__c,
                    BillingPostalCode = firstFive(r.stagingRow.Cust_Zip__c)
                ));
            }
            newCustomerByNumber.put(r.custNumber, new Customer_ID__c(Name = r.custNumber));
            pendingCustomerAccountName.put(r.custNumber, r.custName);
        }

        if (!newAccountByName.isEmpty()) {
            insert newAccountByName.values();
            accountsCreatedCount += newAccountByName.size();
            for (Account a : newAccountByName.values()) {
                accountByName.put(a.Name, a);
            }
        }

        if (!newCustomerByNumber.isEmpty()) {
            for (String custNo : newCustomerByNumber.keySet()) {
                Customer_ID__c c = newCustomerByNumber.get(custNo);
                Account acct = accountByName.get(pendingCustomerAccountName.get(custNo));
                c.Account__c = acct.Id;
            }
            insert newCustomerByNumber.values();
            customersCreatedCount += newCustomerByNumber.size();
            for (Customer_ID__c c : newCustomerByNumber.values()) {
                customerByNumber.put(c.Name, c);
            }
        }

        // ---- build junction updates/inserts and close out staging rows ----
        Map<Id, Parcel_Customer__c> junctionsToUpdate = new Map<Id, Parcel_Customer__c>();
        List<Parcel_Customer__c> junctionsToInsert = new List<Parcel_Customer__c>();

        for (RowContext r : rows) {
            if (r.needsOwnerJunction) {
                if (r.ownerChanged && r.existingJunction != null) {
                    junctionsToUpdate.put(r.existingJunction.Id, new Parcel_Customer__c(
                        Id = r.existingJunction.Id,
                        Association_Role__c = ROLE_CARE_OF
                    ));
                    careOfUpdatesCount++;
                }

                Customer_ID__c cust = customerByNumber.get(r.custNumber);
                junctionsToInsert.add(new Parcel_Customer__c(
                    Parcel__c = r.parcel.Id,
                    Customer_ID__c = cust.Id,
                    Account__c = cust.Account__c,
                    Association_Role__c = ROLE_OWNER
                ));
                ownerJunctionsCreatedCount++;

                r.stagingRow.Processing_Status__c = STATUS_PROCESSED;
            } else {
                String linkedAccountName = r.existingJunction.Account__r.Name;
                String prcOwnName = String.isNotBlank(r.stagingRow.Prc_Own_Name__c) ? r.stagingRow.Prc_Own_Name__c.trim() : '';
                Boolean nameMatches = linkedAccountName != null && linkedAccountName.equalsIgnoreCase(prcOwnName);

                if (nameMatches) {
                    r.stagingRow.Processing_Status__c = STATUS_PROCESSED;
                } else {
                    r.stagingRow.Processing_Status__c = STATUS_REVIEW;
                    r.stagingRow.Error_Message__c = 'Existing Owner Account name (' + linkedAccountName
                        + ') does not match prc_own_name (' + r.stagingRow.Prc_Own_Name__c + ').';
                    rowsFlaggedForReviewCount++;
                }
            }
            stagingToClose.add(r.stagingRow);
        }

        if (!junctionsToUpdate.isEmpty()) {
            update junctionsToUpdate.values();
        }
        if (!junctionsToInsert.isEmpty()) {
            insert junctionsToInsert;
        }
        if (!stagingToClose.isEmpty()) {
            update stagingToClose;
        }
    }

    public void finish(Database.BatchableContext bc) {
        AsyncApexJob job = [SELECT Id, CreatedBy.Email FROM AsyncApexJob WHERE Id = :bc.getJobId()];

        String body = 'Parcel-Customer monthly sync finished.\n\n'
            + 'Parcels updated: ' + parcelsUpdatedCount + '\n'
            + 'Accounts created: ' + accountsCreatedCount + '\n'
            + 'Customer_ID__c created: ' + customersCreatedCount + '\n'
            + 'Owner junctions created: ' + ownerJunctionsCreatedCount + '\n'
            + 'Junctions set to Care Of: ' + careOfUpdatesCount + '\n'
            + 'Rows flagged for review: ' + rowsFlaggedForReviewCount + '\n'
            + 'Rows errored: ' + rowsErroredCount;

        Messaging.SingleEmailMessage mail = new Messaging.SingleEmailMessage();
        mail.setToAddresses(new String[] { job.CreatedBy.Email });
        mail.setSubject('Parcel-Customer Monthly Sync Summary');
        mail.setPlainTextBody(body);
        Messaging.sendEmail(new Messaging.SingleEmailMessage[] { mail });
    }

    private static String trimJoin(String a, String b) {
        String left = String.isBlank(a) ? '' : a.trim();
        String right = String.isBlank(b) ? '' : b.trim();
        if (String.isBlank(left)) {
            return right;
        }
        if (String.isBlank(right)) {
            return left;
        }
        return left + ' ' + right;
    }

    private static String firstFive(String zip) {
        if (String.isBlank(zip)) {
            return null;
        }
        String z = zip.trim();
        return z.length() > 5 ? z.substring(0, 5) : z;
    }

    private class RowContext {
        Parcel_Customer_Staging__c stagingRow;
        Parcel__c parcel;
        String parcelNumber;
        String custNumber;
        String custName;
        Boolean ownerChanged;
        Parcel_Customer__c existingJunction;
        Boolean needsOwnerJunction = false;

        RowContext(Parcel_Customer_Staging__c stagingRow, Parcel__c parcel, String parcelNumber,
                   String custNumber, String custName, Boolean ownerChanged) {
            this.stagingRow = stagingRow;
            this.parcel = parcel;
            this.parcelNumber = parcelNumber;
            this.custNumber = custNumber;
            this.custName = custName;
            this.ownerChanged = ownerChanged;
        }
    }
}

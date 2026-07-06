/**
 * Monthly entry point for ParcelCustomerSyncBatch, once the source file has
 * been loaded into Parcel_Customer_Staging__c (e.g. via Data Loader).
 * Schedule with: System.schedule('Parcel Customer Monthly Sync', '<CRON>', new ParcelCustomerSyncScheduler());
 */
public class ParcelCustomerSyncScheduler implements Schedulable {
    public void execute(SchedulableContext sc) {
        Database.executeBatch(new ParcelCustomerSyncBatch(), 200);
    }
}

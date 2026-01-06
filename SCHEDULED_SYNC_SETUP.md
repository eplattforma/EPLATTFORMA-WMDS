# Setting Up Scheduled Data Warehouse Sync

This guide explains how to automatically run data warehouse syncs on a schedule using Replit's scheduled deployments.

## What Gets Scheduled

You can schedule either:
- **Full sync**: Complete refresh of all items, categories, brands, seasons, and attributes 1-6
- **Incremental sync**: Only syncs items modified since last sync (faster, recommended for daily runs)

## Setup Instructions

### 1. Create a Scheduled Deployment

1. Click on the **Publishing** tool (left sidebar)
2. Select **"Scheduled"** tab
3. Click **"Set up your published app"**

### 2. Configure Full Sync (Daily)

Create a scheduled deployment with these settings:

**Name:** `Data Warehouse - Full Sync`

**Command:**
```
python /home/runner/workspace/sync_scheduler.py full
```

**Schedule:** Use natural language like:
- `Every day at 2 AM` 
- `Every Sunday at 3 AM` (weekly)
- Or enter cron expression: `0 2 * * *` (daily at 2 AM UTC)

**Timeout:** Leave as default (11 hours - more than enough)

### 3. Configure Incremental Sync (Optional - for faster daily updates)

Create another scheduled deployment:

**Name:** `Data Warehouse - Incremental Sync`

**Command:**
```
python /home/runner/workspace/sync_scheduler.py incremental
```

**Schedule:** `Every day at 6 AM`

This runs after the full sync completes and captures any changes made during the day.

## Monitoring & Logs

### View Sync Logs

All sync runs create logs in the `logs/` directory with timestamps:
```
logs/sync_20251123_120000.log
logs/sync_20251123_130000.log
```

Each log contains:
- Start and end timestamps
- Items synced
- Changes detected (for incremental)
- Any errors encountered

### Check Deployment Status

1. Go to **Publishing** → **Scheduled**
2. View your deployments
3. Click on a deployment to see:
   - Last run time
   - Status (success/failed)
   - Run history
   - Error alerts

## What Gets Logged

Each sync run records:

**Full Sync:**
```
================================
STARTING FULL DATA WAREHOUSE SYNC
Timestamp: 2025-11-23T02:00:00
================================
Syncing categories...
Categories synced successfully: 156
Syncing brands...
Brands synced successfully: 42
Syncing seasons...
Seasons synced successfully: 8
Syncing attributes 1-6...
Attribute 1 synced successfully: 12
...
Syncing items...
Total items updated: 4053
================================
✅ FULL SYNC COMPLETED SUCCESSFULLY
================================
```

**Incremental Sync:**
```
================================
STARTING INCREMENTAL DATA WAREHOUSE SYNC
Timestamp: 2025-11-23T06:00:00
================================
Items found with PS365 modifications: 23
Items with actual data changes: 8

Items that will be updated:
  1. [NEW] ITEM-001 - New Product Name
  2. [UPDATED] ITEM-045 - Product Name
     Changes: name: 'Old Name' → 'New Name' | attribute1: ABC → XYZ
...
================================
✅ INCREMENTAL SYNC COMPLETED SUCCESSFULLY
================================
```

## Recommended Schedule

For optimal performance:

- **Full Sync**: Every Sunday at 2 AM (weekly complete refresh)
- **Incremental Sync**: Every day at 6 AM (catches daily changes)

This ensures:
- Complete data accuracy once per week
- Daily capture of changes
- No overlap between syncs
- Logs for audit trail

## Environment Variables

Make sure these are set in your Replit environment:
- `DATABASE_URL` - Development database (auto-set by Replit)
- `PS365_API_KEY` - Your Powersoft365 API key
- `PS365_ORG_ID` - Your organization ID

These are used automatically by the sync scripts.

## Troubleshooting

### Sync fails or times out?
- Check `logs/` directory for error details
- Verify API credentials in environment variables
- Full sync may take 5-10 minutes for 4000+ items

### Logs not appearing?
- Logs are in `logs/sync_TIMESTAMP.log`
- Scheduled deployments have separate log storage in Publishing dashboard

### Database connection error?
- Verify `DATABASE_URL` is set correctly
- Check database is accessible
- Review error log for specific connection issue

---
name: Picking reports must bucket by local day
description: item_time_tracking timestamps are UTC; daily reports must convert to the configured system timezone before bucketing.
---

Reports over `item_time_tracking` (and shifts) must define a "work day" in the configured system timezone (Setting `system_timezone`, default Europe/Athens; scheduler runs Cairo).

**Why:** timestamps are stored as naive UTC; bucketing by `item_started::date` attributes early-local-morning picks to the prior day and late check-outs to an empty next-day report. The legacy views in the picking report migrations still bucket by UTC date (tracked as tech debt).

**How to apply:** convert local midnight bounds to naive UTC for range filters (see the day-bounds helper in the picker shift report service) and bucket with `item_started AT TIME ZONE 'UTC' AT TIME ZONE :tz`. Also keep one shared "completed pick" predicate (`NOT was_skipped AND total_item_time > 0`) for every numerator/denominator so accuracy/skip metrics stay consistent.

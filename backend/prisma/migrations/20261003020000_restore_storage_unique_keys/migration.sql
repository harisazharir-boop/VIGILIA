-- The `add_storage_report` migration swapped both tables to add their foreign
-- keys, but the recreation of the unique indexes after the rename was left
-- out. `StorageReport.deviceId` and `CleanupPlan.deviceId` are `@unique` in
-- `schema.prisma` and every write is an `upsert` keyed on them, so without
-- these indexes SQLite rejects every upsert with
-- "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint".
--
-- The same shape of bug is in `init` (Telemetry) and
-- `add_application_control` (ApplicationCatalog): each created the unique
-- index, then swapped the table for a foreign-key-bearing copy that never
-- got the index back. Every upsert on those three `deviceId` columns was
-- failing the same way, which silently dropped all telemetry and application
-- catalog pushes from a real agent.
--
-- Recreate them now. The tables were empty in dev, so there is no data to
-- deduplicate before applying the constraint.

CREATE UNIQUE INDEX IF NOT EXISTS "StorageReport_deviceId_key" ON "StorageReport"("deviceId");

CREATE UNIQUE INDEX IF NOT EXISTS "CleanupPlan_deviceId_key" ON "CleanupPlan"("deviceId");

CREATE UNIQUE INDEX IF NOT EXISTS "Telemetry_deviceId_key" ON "Telemetry"("deviceId");

CREATE UNIQUE INDEX IF NOT EXISTS "ApplicationCatalog_deviceId_key" ON "ApplicationCatalog"("deviceId");

CREATE INDEX IF NOT EXISTS "StorageReport_deviceId_updatedAt_idx" ON "StorageReport"("deviceId", "updatedAt");

CREATE INDEX IF NOT EXISTS "CleanupPlan_deviceId_updatedAt_idx" ON "CleanupPlan"("deviceId", "updatedAt");

-- CreateTable
CREATE TABLE "StorageReport" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "summary" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "CleanupPlan" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "plan" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateIndex
CREATE UNIQUE INDEX "StorageReport_deviceId_key" ON "StorageReport"("deviceId");

-- CreateIndex
CREATE INDEX "StorageReport_deviceId_updatedAt_idx" ON "StorageReport"("deviceId", "updatedAt");

-- CreateIndex
CREATE UNIQUE INDEX "CleanupPlan_deviceId_key" ON "CleanupPlan"("deviceId");

-- CreateIndex
CREATE INDEX "CleanupPlan_deviceId_updatedAt_idx" ON "CleanupPlan"("deviceId", "updatedAt");

-- AddForeignKey
PRAGMA foreign_keys=off;

CREATE TABLE "new_StorageReport" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "summary" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "StorageReport_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_StorageReport" ("id", "deviceId", "summary", "createdAt", "updatedAt")
SELECT "id", "deviceId", "summary", "createdAt", "updatedAt" FROM "StorageReport";

DROP TABLE "StorageReport";

ALTER TABLE "new_StorageReport" RENAME TO "StorageReport";

CREATE TABLE "new_CleanupPlan" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "plan" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "CleanupPlan_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_CleanupPlan" ("id", "deviceId", "plan", "createdAt", "updatedAt")
SELECT "id", "deviceId", "plan", "createdAt", "updatedAt" FROM "CleanupPlan";

DROP TABLE "CleanupPlan";

ALTER TABLE "new_CleanupPlan" RENAME TO "CleanupPlan";

PRAGMA foreign_key_check;

PRAGMA foreign_keys=on;

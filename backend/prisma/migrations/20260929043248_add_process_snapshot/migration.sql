-- CreateTable
CREATE TABLE "ProcessSnapshot" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "snapshot" TEXT NOT NULL,
    "totalCount" INTEGER NOT NULL,
    "limit" INTEGER NOT NULL,
    "timestamp" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CreateIndex
CREATE INDEX "ProcessSnapshot_deviceId_timestamp_idx" ON "ProcessSnapshot"("deviceId", "timestamp");

-- AddForeignKey
PRAGMA foreign_keys=off;

CREATE TABLE "new_ProcessSnapshot" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "snapshot" TEXT NOT NULL,
    "totalCount" INTEGER NOT NULL,
    "limit" INTEGER NOT NULL,
    "timestamp" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ProcessSnapshot_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_ProcessSnapshot" ("id", "deviceId", "snapshot", "totalCount", "limit", "timestamp")
SELECT "id", "deviceId", "snapshot", "totalCount", "limit", "timestamp" FROM "ProcessSnapshot";

DROP TABLE "ProcessSnapshot";

ALTER TABLE "new_ProcessSnapshot" RENAME TO "ProcessSnapshot";

PRAGMA foreign_key_check;

PRAGMA foreign_keys=on;

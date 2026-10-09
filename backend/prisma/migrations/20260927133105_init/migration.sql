-- CreateTable
CREATE TABLE "Device" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "name" TEXT NOT NULL,
    "os" TEXT NOT NULL,
    "hostname" TEXT NOT NULL,
    "agentToken" TEXT,
    "commandToken" TEXT,
    "status" TEXT NOT NULL DEFAULT 'offline',
    "lastSeen" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateTable
CREATE TABLE "Telemetry" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "sample" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateIndex
CREATE UNIQUE INDEX "Device_agentToken_key" ON "Device"("agentToken");

-- CreateIndex
CREATE INDEX "Device_name_hostname_idx" ON "Device"("name", "hostname");

-- CreateIndex
CREATE INDEX "Device_status_lastSeen_idx" ON "Device"("status", "lastSeen");

-- CreateIndex
CREATE UNIQUE INDEX "Telemetry_deviceId_key" ON "Telemetry"("deviceId");

-- AddForeignKey
PRAGMA foreign_keys=off;

CREATE TABLE "new_Telemetry" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "sample" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "Telemetry_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_Telemetry" ("id", "deviceId", "sample", "createdAt", "updatedAt") SELECT "id", "deviceId", "sample", "createdAt", "updatedAt" FROM "Telemetry";

DROP TABLE "Telemetry";

ALTER TABLE "new_Telemetry" RENAME TO "Telemetry";

PRAGMA foreign_key_check;

PRAGMA foreign_keys=on;

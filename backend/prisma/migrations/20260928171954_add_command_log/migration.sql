-- CreateTable
CREATE TABLE "CommandLog" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "command" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "note" TEXT,
    "target" TEXT,
    "result" TEXT,
    "requestId" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    "expiresAt" DATETIME
);

-- CreateIndex
CREATE UNIQUE INDEX "CommandLog_requestId_key" ON "CommandLog"("requestId");

-- CreateIndex
CREATE INDEX "CommandLog_deviceId_createdAt_idx" ON "CommandLog"("deviceId", "createdAt");

-- CreateIndex
CREATE INDEX "CommandLog_status_expiresAt_idx" ON "CommandLog"("status", "expiresAt");

-- AddForeignKey
PRAGMA foreign_keys=off;

CREATE TABLE "new_CommandLog" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "command" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "note" TEXT,
    "target" TEXT,
    "result" TEXT,
    "requestId" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    "expiresAt" DATETIME,
    CONSTRAINT "CommandLog_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_CommandLog" ("id", "deviceId", "command", "status", "note", "target", "result", "requestId", "createdAt", "updatedAt", "expiresAt")
SELECT "id", "deviceId", "command", "status", "note", "target", "result", "requestId", "createdAt", "updatedAt", "expiresAt" FROM "CommandLog";

DROP TABLE "CommandLog";

ALTER TABLE "new_CommandLog" RENAME TO "CommandLog";

PRAGMA foreign_key_check;

PRAGMA foreign_keys=on;

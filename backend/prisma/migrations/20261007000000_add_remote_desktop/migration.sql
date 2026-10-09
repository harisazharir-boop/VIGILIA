CREATE TABLE "RemoteSession" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "sessionId" TEXT NOT NULL,
  "deviceId" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "capabilities" JSONB NOT NULL,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "lastActivityAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expiresAt" DATETIME NOT NULL,
  "endedAt" DATETIME,
  "reason" TEXT,
  CONSTRAINT "RemoteSession_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX "RemoteSession_sessionId_key" ON "RemoteSession"("sessionId");
CREATE INDEX "RemoteSession_deviceId_status_idx" ON "RemoteSession"("deviceId", "status");
CREATE INDEX "RemoteSession_expiresAt_idx" ON "RemoteSession"("expiresAt");
CREATE TABLE "RemoteAudit" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "sessionId" TEXT,
  "deviceId" TEXT NOT NULL,
  "operation" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "reason" TEXT,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "RemoteAudit_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX "RemoteAudit_deviceId_createdAt_idx" ON "RemoteAudit"("deviceId", "createdAt");

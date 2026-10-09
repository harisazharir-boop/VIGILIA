CREATE TABLE "HardwareAudit" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "requestId" TEXT,
  "deviceId" TEXT NOT NULL,
  "control" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "reason" TEXT,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "HardwareAudit_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX "HardwareAudit_requestId_key" ON "HardwareAudit"("requestId");
CREATE INDEX "HardwareAudit_deviceId_createdAt_idx" ON "HardwareAudit"("deviceId", "createdAt");
CREATE TABLE "AiAudit" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "requestId" TEXT NOT NULL,
  "deviceId" TEXT NOT NULL,
  "tool" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "reason" TEXT,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "AiAudit_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX "AiAudit_requestId_key" ON "AiAudit"("requestId");
CREATE INDEX "AiAudit_deviceId_createdAt_idx" ON "AiAudit"("deviceId", "createdAt");
CREATE TABLE "AiAlert" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "deviceId" TEXT NOT NULL,
  "severity" TEXT NOT NULL,
  "category" TEXT NOT NULL,
  "message" TEXT NOT NULL,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "AiAlert_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX "AiAlert_deviceId_createdAt_idx" ON "AiAlert"("deviceId", "createdAt");
CREATE TABLE "AiMessage" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "deviceId" TEXT NOT NULL,
  "role" TEXT NOT NULL,
  "content" TEXT NOT NULL,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "AiMessage_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX "AiMessage_deviceId_createdAt_idx" ON "AiMessage"("deviceId", "createdAt");
CREATE TABLE "AiConfirmation" (
  "id" TEXT NOT NULL PRIMARY KEY,
  "requestId" TEXT NOT NULL,
  "deviceId" TEXT NOT NULL,
  "action" TEXT NOT NULL,
  "status" TEXT NOT NULL,
  "expiresAt" DATETIME NOT NULL,
  "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "decidedAt" DATETIME,
  CONSTRAINT "AiConfirmation_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE UNIQUE INDEX "AiConfirmation_requestId_key" ON "AiConfirmation"("requestId");
CREATE INDEX "AiConfirmation_deviceId_status_expiresAt_idx" ON "AiConfirmation"("deviceId", "status", "expiresAt");
CREATE TABLE "GuardianPreferences" (
  "deviceId" TEXT NOT NULL PRIMARY KEY,
  "healthAlerts" BOOLEAN NOT NULL DEFAULT true,
  "securityAlerts" BOOLEAN NOT NULL DEFAULT true,
  "updatedAt" DATETIME NOT NULL,
  CONSTRAINT "GuardianPreferences_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

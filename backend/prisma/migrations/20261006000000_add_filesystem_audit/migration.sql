-- CreateTable
CREATE TABLE "FilesystemAudit" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "requestId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "operation" TEXT NOT NULL,
    "targetHash" TEXT,
    "status" TEXT NOT NULL,
    "reason" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" DATETIME,
    CONSTRAINT "FilesystemAudit_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateIndex
CREATE UNIQUE INDEX "FilesystemAudit_requestId_key" ON "FilesystemAudit"("requestId");
CREATE INDEX "FilesystemAudit_deviceId_createdAt_idx" ON "FilesystemAudit"("deviceId", "createdAt");
CREATE INDEX "FilesystemAudit_status_completedAt_idx" ON "FilesystemAudit"("status", "completedAt");

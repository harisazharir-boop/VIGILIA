-- CreateTable
CREATE TABLE "OwnerState" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "since" REAL NOT NULL,
    "lastAuthenticatedAt" REAL,
    "failedAttempts" INTEGER NOT NULL DEFAULT 0,
    "capabilities" JSONB NOT NULL,
    "lastDailyCleanupDate" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "OwnerState_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "DailyCleanupState" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "lastCleanupDate" TEXT,
    "lastCleanupAt" REAL,
    "lastResult" TEXT,
    "config" JSONB NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "DailyCleanupState_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "SecurityEvent" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "eventType" TEXT NOT NULL,
    "severity" TEXT NOT NULL,
    "description" TEXT NOT NULL,
    "metadata" JSONB,
    "requestId" TEXT,
    "since" REAL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "SecurityEvent_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);


-- CreateIndex
CREATE UNIQUE INDEX "OwnerState_deviceId_key" ON "OwnerState"("deviceId");

-- CreateIndex
CREATE UNIQUE INDEX "DailyCleanupState_deviceId_key" ON "DailyCleanupState"("deviceId");

-- CreateIndex
CREATE INDEX "SecurityEvent_deviceId_createdAt_idx" ON "SecurityEvent"("deviceId", "createdAt");

-- CreateIndex
CREATE INDEX "SecurityEvent_eventType_createdAt_idx" ON "SecurityEvent"("eventType", "createdAt");

-- CreateTable
CREATE TABLE "ApplicationCatalog" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "catalog" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateIndex
CREATE UNIQUE INDEX "ApplicationCatalog_deviceId_key" ON "ApplicationCatalog"("deviceId");

-- AddForeignKey
PRAGMA foreign_keys=off;

CREATE TABLE "new_ApplicationCatalog" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "deviceId" TEXT NOT NULL,
    "catalog" TEXT NOT NULL,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "ApplicationCatalog_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE
);

INSERT INTO "new_ApplicationCatalog" ("id", "deviceId", "catalog", "createdAt", "updatedAt")
SELECT "id", "deviceId", "catalog", "createdAt", "updatedAt" FROM "ApplicationCatalog";

DROP TABLE "ApplicationCatalog";

ALTER TABLE "new_ApplicationCatalog" RENAME TO "ApplicationCatalog";

PRAGMA foreign_key_check;

PRAGMA foreign_keys=on;

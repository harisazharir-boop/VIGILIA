from __future__ import annotations
import unittest
import sqlite3
import subprocess
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
class DatabaseScriptsTests(unittest.TestCase):
    def test_migration_script_uses_backup_and_never_reset_or_push(self):
        text = (ROOT / 'deploy/database-migrate.sh').read_text()
        self.assertIn('sqlite-backup.py', text); self.assertIn('prisma" migrate deploy', text)
        self.assertNotIn('migrate reset', text); self.assertNotIn('db push', text); self.assertIn('database outside install root refused', text)
    def test_restore_requires_stopped_backend_and_restricted_backup_path(self):
        text = (ROOT / 'deploy/database-restore.sh').read_text()
        self.assertIn('backend must be stopped before restore', text); self.assertIn('backup path refused', text); self.assertIn('pre-restore-', text)
    def test_installer_deploys_required_database_artifacts(self):
        text = (ROOT / 'deploy/install-linux.sh').read_text()
        for artifact in ('database-migrate.sh', 'database-restore.sh', 'sqlite-backup.py', 'prisma-preflight.mjs'):
            self.assertIn(artifact, text)
        self.assertIn('chmod 0750 "$ROOT/deploy/database-migrate.sh"', text)
    def test_normal_installer_preserves_production_db_and_never_migrates(self):
        text = (ROOT / 'deploy/install-linux.sh').read_text()
        self.assertIn('--exclude backend/prisma/prod.db', text)
        self.assertIn('--exclude backend/prisma/prod.db-wal', text)
        self.assertIn('--exclude backend/prisma/prod.db-shm', text)
        self.assertNotIn('prisma migrate deploy', text)
        self.assertNotIn('&& node deploy/prisma-preflight.mjs', text)
    def test_sqlite_backup_preserves_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'source.db', Path(tmp) / 'backup.db'
            db = sqlite3.connect(source); db.execute('create table sample (value text)'); db.execute("insert into sample values ('kept')"); db.commit(); db.close()
            subprocess.run(['python3', str(ROOT / 'deploy/sqlite-backup.py'), str(source), str(target)], check=True)
            restored = sqlite3.connect(target); self.assertEqual(restored.execute('select value from sample').fetchone()[0], 'kept'); restored.close()
    def test_backup_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['python3', str(ROOT / 'deploy/sqlite-backup.py'), str(Path(tmp) / 'missing.db'), str(Path(tmp) / 'backup.db')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0); self.assertIn('source database missing', result.stderr)

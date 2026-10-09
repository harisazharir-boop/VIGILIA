"""Phase 6 Windows policy contract tests. Run on Linux too; Windows-only reparse test is conditional."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from agents.common.filesystem import SafeFileManager


class WindowsFilesystemPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "User"; self.docs = self.home / "Documents"
        self.docs.mkdir(parents=True); (self.docs / "Report.TXT").write_text("ok", encoding="utf-8")
        self.manager = SafeFileManager(home=self.home)

    def tearDown(self): self.tmp.cleanup()

    def test_drive_and_traversal_are_not_protocol_inputs(self):
        denied = self.manager.execute({"requestId": "req_win", "operation": "FILE_LIST", "rootId": "C:\\Windows"})
        self.assertEqual(denied["status"], "NOT_ALLOWED")
        search = self.manager.execute({"requestId": "req_win2", "operation": "FILE_SEARCH", "rootId": "DOCUMENTS", "query": "..\\Windows"})
        self.assertEqual(search["code"], "VALIDATION_FAILED")

    def test_credentials_env_keys_and_vigilia_are_not_listed(self):
        for name in (".env", "id_rsa", "client.pem", "credentials.json", ".vigilia"):
            path = self.docs / name
            if name == ".vigilia": path.mkdir()
            else: path.write_text("secret", encoding="utf-8")
        names = [item["name"] for item in self.manager.execute({"requestId": "req_win3", "operation": "FILE_LIST", "rootId": "DOCUMENTS"})["entries"]]
        self.assertEqual(names, ["Report.TXT"])

    def test_stale_and_wrong_handle_are_denied(self):
        result = self.manager.execute({"requestId": "req_win4", "operation": "FILE_LIST", "rootId": "DOCUMENTS"})
        handle = result["entries"][0]["id"]
        self.manager.handles[handle].expires = 0
        self.assertEqual(self.manager.execute({"requestId": "req_win5", "operation": "FILE_STAT", "entryId": handle})["status"], "NOT_ALLOWED")
        self.assertEqual(self.manager.execute({"requestId": "req_win6", "operation": "FILE_STAT", "entryId": "f_aaaaaaaaaaaaaaaa"})["status"], "NOT_ALLOWED")

    @unittest.skipUnless(sys.platform == "win32", "requires Windows reparse-point support")
    def test_junction_escape_is_not_listed(self):
        outside = Path(self.tmp.name) / "outside"; outside.mkdir()
        junction = self.docs / "escape"
        os.system(f'mklink /J "{junction}" "{outside}"')
        names = [item["name"] for item in self.manager.execute({"requestId": "req_win7", "operation": "FILE_LIST", "rootId": "DOCUMENTS"})["entries"]]
        self.assertNotIn("escape", names)

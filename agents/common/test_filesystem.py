import tempfile
import unittest
from pathlib import Path

from agents.common.filesystem import SafeFileManager


class SafeFileManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name) / "home"
        projects = Path(self.tmp.name) / "projects"
        home.mkdir(); projects.mkdir()
        (home / "Documents").mkdir()
        (home / "Documents" / "note.txt").write_text("hello", encoding="utf-8")
        (home / "Documents" / ".env").write_text("secret", encoding="utf-8")
        self.manager = SafeFileManager(home=home, projects=projects)

    def tearDown(self): self.tmp.cleanup()

    def request(self, operation, **fields):
        return self.manager.execute({"requestId": "req_test", "operation": operation, **fields})

    def test_list_hides_protected_file_and_uses_opaque_ids(self):
        result = self.request("FILE_LIST", rootId="DOCUMENTS")
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual([entry["name"] for entry in result["entries"]], ["note.txt"])
        self.assertTrue(result["entries"][0]["id"].startswith("f_"))
        self.assertNotIn(str(Path(self.tmp.name)), str(result))

    def test_forged_handle_and_traversal_are_denied(self):
        self.assertEqual(self.request("FILE_STAT", entryId="f_aaaaaaaaaaaaaaaa")["status"], "NOT_ALLOWED")
        self.assertEqual(self.request("FILE_SEARCH", rootId="DOCUMENTS", query="../secret")["code"], "VALIDATION_FAILED")

    def test_symlink_escape_is_not_listed(self):
        outside = Path(self.tmp.name) / "outside.txt"; outside.write_text("no", encoding="utf-8")
        link = Path(self.tmp.name) / "home" / "Documents" / "escape"; link.symlink_to(outside)
        names = [entry["name"] for entry in self.request("FILE_LIST", rootId="DOCUMENTS")["entries"]]
        self.assertNotIn("escape", names)

    def test_secret_directories_and_external_projects_root_are_denied(self):
        secret = Path(self.tmp.name) / "home" / "Documents" / ".ssh"; secret.mkdir()
        outside = Path(self.tmp.name) / "outside-projects"; outside.mkdir()
        manager = SafeFileManager(home=Path(self.tmp.name) / "home", projects=outside)
        names = [entry["name"] for entry in manager.execute({"requestId": "req_other", "operation": "FILE_LIST", "rootId": "DOCUMENTS"})["entries"]]
        self.assertNotIn(".ssh", names)
        self.assertEqual(manager.execute({"requestId": "req_projects", "operation": "FILE_LIST", "rootId": "PROJECTS"})["status"], "NOT_ALLOWED")

    def test_upload_collision_rename_and_non_recursive_delete(self):
        listed = self.request("FILE_LIST", rootId="DOCUMENTS")["entries"]
        directory = self.request("FILE_LIST", rootId="HOME")["entries"]
        documents = next(entry for entry in directory if entry["name"] == "Documents")
        upload = self.request("FILE_UPLOAD", destinationId=documents["id"], name="new.txt", size=4)
        self.assertEqual(upload["status"], "SUCCEEDED")
        self.manager.upload_chunk("req_test", 0, b"data")
        completed = self.manager.complete_upload("req_test")
        self.assertEqual(self.request("FILE_RENAME", entryId=completed["entry"]["id"], name=".env") ["status"], "NOT_ALLOWED")
        self.assertEqual(self.request("FILE_DELETE", entryId=documents["id"])["status"], "NOT_ALLOWED")

    def test_download_size_limit(self):
        path = Path(self.tmp.name) / "home" / "Documents" / "large.bin"; path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        listed = self.request("FILE_LIST", rootId="DOCUMENTS")["entries"]
        large = next(entry for entry in listed if entry["name"] == "large.bin")
        self.assertEqual(self.request("FILE_DOWNLOAD", entryId=large["id"])["status"], "NOT_ALLOWED")

    def test_chunked_download_and_upload_cleanup(self):
        listed = self.request("FILE_LIST", rootId="DOCUMENTS")["entries"]
        note = next(entry for entry in listed if entry["name"] == "note.txt")
        started = self.request("FILE_DOWNLOAD", entryId=note["id"])
        self.assertEqual(started["transfer"]["direction"], "download")
        frames = []; self.manager.download_chunks("req_test", frames.append, chunk_size=2)
        self.assertEqual(b"".join(__import__("base64").b64decode(frame["data"]["dataBase64"]) for frame in frames if frame["type"] == "file.transfer.chunk"), b"hello")
        documents = next(entry for entry in self.request("FILE_LIST", rootId="HOME")["entries"] if entry["name"] == "Documents")
        self.assertEqual(self.request("FILE_UPLOAD", destinationId=documents["id"], name="cancel.txt", size=3)["status"], "SUCCEEDED")
        self.manager.upload_chunk("req_test", 0, b"x")
        self.manager.cancel_transfer("req_test")
        self.assertFalse((Path(self.tmp.name) / "home" / "Documents" / ".cancel.txt.vigilia-upload").exists())


if __name__ == "__main__": unittest.main()

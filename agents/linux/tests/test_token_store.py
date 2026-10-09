"""
VIGILIA agent — token storage tests.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.token_store import read_device_id, read_token, write_device_id, write_token  # noqa: E402

LOG = Logger(quiet=True)


class TokenStoreTests(unittest.TestCase):
    def test_device_id_roundtrip_is_restrictive(self):
        path = Path(self.directory) / "token"
        write_device_id(path, "cmdeviceid", LOG)
        self.assertEqual(read_device_id(path, LOG), "cmdeviceid")
        self.assertEqual((path.with_suffix(path.suffix + ".device-id").stat().st_mode & 0o777), 0o600)
    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp(prefix="vigilia-token-")

    def tearDown(self) -> None:
        for entry in Path(self.directory).rglob("*"):
            if entry.is_file():
                entry.chmod(0o600)
        Path(self.directory).chmod(0o700)

    def test_read_returns_none_when_missing(self) -> None:
        self.assertIsNone(read_token(Path(self.directory) / "missing", LOG))

    def test_roundtrip(self) -> None:
        path = Path(self.directory) / "agent.token"
        write_token(path, "vig_abc123", LOG)
        self.assertEqual(read_token(path, LOG), "vig_abc123")

    def test_write_creates_parent_directories(self) -> None:
        path = Path(self.directory) / "nested" / "dir" / "agent.token"
        write_token(path, "vig_xyz", LOG)
        self.assertEqual(read_token(path, LOG), "vig_xyz")

    def test_write_is_restrictive(self) -> None:
        path = Path(self.directory) / "agent.token"
        write_token(path, "vig_secret", LOG)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o077, 0, "the token file must not be group/world readable")

    def test_read_of_empty_file_returns_none(self) -> None:
        path = Path(self.directory) / "agent.token"
        path.write_text("   \n", encoding="utf-8")
        self.assertIsNone(read_token(path, LOG))


if __name__ == "__main__":
    unittest.main()

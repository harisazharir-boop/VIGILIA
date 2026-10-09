"""
VIGILIA agent — shared configuration tests.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.config import load_config  # noqa: E402


class ConfigTests(unittest.TestCase):
    """Every test writes its own .env, so snapshot the process environment and
    restore it: _load_dotenv mutates os.environ and a leaked value would make a
    later unrelated test fail."""

    def setUp(self) -> None:
        self._env_snapshot = dict(os.environ)
        # The developer shell may have exported the project .env, and
        # _load_dotenv never overrides an existing value — drop the keys the
        # suite sets so each test's own file wins.
        for key in list(os.environ):
            if key.startswith("VIGILIA_") or key in ("DATABASE_URL", "NODE_ENV", "PORT", "HOST", "LOG_LEVEL"):
                del os.environ[key]

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def _write_env(self, contents: str) -> str:
        directory = tempfile.mkdtemp(prefix="vigilia-cfg-")
        (Path(directory) / ".env").write_text(contents, encoding="utf-8")
        return directory

    def test_defaults_when_nothing_is_set(self) -> None:
        directory = tempfile.mkdtemp(prefix="vigilia-cfg-")
        config = load_config(Path(directory))
        self.assertEqual(config.agent_name, "VIGILIA Laptop")
        self.assertEqual(config.backend_url, "http://127.0.0.1:3000")
        self.assertEqual(config.telemetry_interval, 5)
        self.assertEqual(config.heartbeat_interval, 10)
        self.assertFalse(config.quiet)

    def test_reads_values_from_a_dotenv_file(self) -> None:
        directory = self._write_env(
            "VIGILIA_AGENT_NAME=Test Laptop\n"
            "VIGILIA_BACKEND_URL=http://10.0.0.1:4000\n"
            "VIGILIA_TELEMETRY_INTERVAL=20\n"
            "VIGILIA_HEARTBEAT_INTERVAL=60\n"
            "VIGILIA_AGENT_QUIET=1\n"
        )
        config = load_config(Path(directory))
        self.assertEqual(config.agent_name, "Test Laptop")
        self.assertEqual(config.backend_url, "http://10.0.0.1:4000")
        self.assertEqual(config.telemetry_interval, 20)
        self.assertEqual(config.heartbeat_interval, 60)
        self.assertTrue(config.quiet)

    def test_quoted_values_are_unwrapped(self) -> None:
        directory = self._write_env('VIGILIA_BACKEND_URL="http://quoted:3000"\n')
        config = load_config(Path(directory))
        self.assertEqual(config.backend_url, "http://quoted:3000")

    def test_process_environment_wins_over_the_dotenv_file(self) -> None:
        directory = self._write_env("VIGILIA_BACKEND_URL=http://file:3000\n")
        previous = os.environ.get("VIGILIA_BACKEND_URL")
        os.environ["VIGILIA_BACKEND_URL"] = "http://env:3000"
        try:
            config = load_config(Path(directory))
        finally:
            if previous is None:
                os.environ.pop("VIGILIA_BACKEND_URL", None)
            else:
                os.environ["VIGILIA_BACKEND_URL"] = previous
        self.assertEqual(config.backend_url, "http://env:3000")

    def test_invalid_interval_raises(self) -> None:
        directory = self._write_env("VIGILIA_TELEMETRY_INTERVAL=notanumber\n")
        with self.assertRaises(ValueError):
            load_config(Path(directory))

    def test_zero_interval_raises(self) -> None:
        directory = self._write_env("VIGILIA_HEARTBEAT_INTERVAL=0\n")
        with self.assertRaises(ValueError):
            load_config(Path(directory))

    def test_ws_url_is_derived_from_the_backend_url(self) -> None:
        cases = {
            "http://127.0.0.1:3000": "ws://127.0.0.1:3000/ws",
            "https://example.com/": "wss://example.com/ws",
            "http://localhost:3000/some/path": "ws://localhost:3000/some/path/ws",
            "ws://already.a.socket/ws": "ws://already.a.socket/ws",
        }
        for backend_url, expected in cases.items():
            with self.subTest(backend_url=backend_url):
                # _load_dotenv never overrides an existing value, and the
                # previous iteration leaked VIGILIA_BACKEND_URL into
                # os.environ — drop it so this iteration's own file wins.
                os.environ.pop("VIGILIA_BACKEND_URL", None)
                directory = self._write_env(f"VIGILIA_BACKEND_URL={backend_url}\n")
                config = load_config(Path(directory))
                self.assertEqual(config.ws_url, expected)

    def test_endpoint_urls(self) -> None:
        directory = self._write_env("VIGILIA_BACKEND_URL=http://127.0.0.1:3000/\n")
        config = load_config(Path(directory))
        self.assertEqual(config.register_url, "http://127.0.0.1:3000/api/devices/register")
        self.assertEqual(config.verify_url, "http://127.0.0.1:3000/api/auth/verify")

    def test_token_file_default(self) -> None:
        directory = tempfile.mkdtemp(prefix="vigilia-cfg-")
        config = load_config(Path(directory))
        self.assertEqual(config.token_file, Path(".agent-token"))
        self.assertIsNone(config.token)


if __name__ == "__main__":
    unittest.main()

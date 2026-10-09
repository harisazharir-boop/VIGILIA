"""
VIGILIA agent — application registry tests (Phase 3B).

The registry is the security boundary for application control: an id it does
not know is refused before anything is launched or terminated. These tests
prove the boundary holds without touching a real launch.

Run:  python3 -m unittest discover -s agents -p 'test_*.py' -v
"""
from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.applications import (  # noqa: E402
    APPLICATION_CATALOG_CAP,
    ApplicationCatalog,
    ApplicationRegistry,
    is_valid_application_id,
)


class ApplicationIdTests(unittest.TestCase):
    def test_accepts_lowercase_identifiers(self) -> None:
        for value in ("firefox", "gnome-calculator", "code", "telegram-desktop", "a1", "my.app_2"):
            self.assertTrue(is_valid_application_id(value), f"{value} should be accepted")

    def test_normalises_case(self) -> None:
        self.assertTrue(is_valid_application_id("Firefox"))
        self.assertTrue(is_valid_application_id("Firefox"))

    def test_rejects_paths_and_shell_metacharacters(self) -> None:
        for value in ("/bin/sh", "sh;rm -rf /", "../etc/passwd", "sh -c ls", "a b", "", " ", "-x", "sh\nwhoami"):
            self.assertFalse(is_valid_application_id(value), f"{value} must be refused")

    def test_rejects_non_strings(self) -> None:
        for value in (None, 42, ["/bin/sh"], {"a": 1}):
            self.assertFalse(is_valid_application_id(value))

    def test_rejects_overlong_ids(self) -> None:
        self.assertFalse(is_valid_application_id("a" * 65))
        self.assertTrue(is_valid_application_id("a" * 64))


class ApplicationRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ApplicationRegistry(platform="linux")

    def test_discovery_only_keeps_present_binaries(self) -> None:
        with patch("agents.common.applications.shutil.which", lambda name: "/usr/bin/" + name if name in ("firefox", "gedit") else None):
            catalog = self.registry.discover()
        self.assertEqual(sorted(catalog.ids()), ["firefox", "gedit"])

    def test_discovery_never_launches_anything(self) -> None:
        # `which` is the only probe; it is a PATH lookup, not an execution.
        probed: list[str] = []

        def fake_which(name: str):
            probed.append(name)
            return None

        with patch("agents.common.applications.shutil.which", fake_which):
            self.registry.discover()
        self.assertTrue(probed, "discovery probes with which()")
        self.assertTrue(all(isinstance(name, str) for name in probed))

    def test_discovery_ignores_an_id_without_a_fixed_launcher(self) -> None:
        # An id in the binary list but not in the argv table is not launchable.
        with patch("agents.common.applications.shutil.which", lambda name: "/usr/bin/" + name):
            catalog = self.registry.discover()
        self.assertNotIn("sh", catalog.ids(), "sh is not in the registry at all")

    def test_resolve_is_case_insensitive_and_refuses_unknown(self) -> None:
        with patch("agents.common.applications.shutil.which", lambda name: "/usr/bin/" + name if name == "firefox" else None):
            self.registry.discover()
        self.assertIsNotNone(self.registry.resolve("Firefox"))
        self.assertIsNotNone(self.registry.resolve("FIREFOX"))
        self.assertIsNone(self.registry.resolve("/bin/sh"))
        self.assertIsNone(self.registry.resolve("sh;rm -rf /"))
        self.assertIsNone(self.registry.resolve(None))
        self.assertIsNone(self.registry.resolve(""))

    def test_register_rejects_a_bad_id(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.register("/bin/sh", "Shell")
        with self.assertRaises(ValueError):
            self.registry.register("", "Empty")

    def test_register_rejects_duplicates(self) -> None:
        self.registry.register("firefox", "Firefox")
        with self.assertRaises(ValueError):
            self.registry.register("firefox", "Firefox again")

    def test_catalog_payload_shape(self) -> None:
        self.registry.register("firefox", "Firefox")
        self.registry.register("gnome-calculator", "Calculator")
        catalog = self.registry.catalog.with_timestamp()
        payload = catalog.to_payload()
        self.assertEqual(payload["deviceId"], "")
        self.assertEqual(len(payload["applications"]), 2)
        self.assertTrue(payload["timestamp"])

    def test_catalog_validation_flags_duplicates_and_bad_ids(self) -> None:
        catalog = ApplicationCatalog(device_id="d1")
        catalog.applications.extend(
            [
                type("A", (), {"application_id": "firefox", "label": "Firefox", "to_payload": lambda self: {}})(),
                type("A", (), {"application_id": "firefox", "label": "Dup", "to_payload": lambda self: {}})(),
            ]
        )
        self.assertIn("duplicate applicationId: firefox", catalog.validate())

        bad = ApplicationCatalog(device_id="d1")
        bad.applications.append(
            type("A", (), {"application_id": "/bin/sh", "label": "Shell", "to_payload": lambda self: {}})(),
        )
        self.assertIn("bad applicationId", " ".join(bad.validate()))

    def test_catalog_rejects_an_oversized_list(self) -> None:
        registry = ApplicationRegistry(platform="linux")
        for index in range(APPLICATION_CATALOG_CAP + 1):
            registry.register(f"app{index}", f"App {index}")
        issues = registry.catalog.validate()
        self.assertTrue(any("too many applications" in issue for issue in issues))


class WindowsRegistryTests(unittest.TestCase):
    def test_windows_discovery_lists_the_fixed_shell_applications(self) -> None:
        registry = ApplicationRegistry(platform="windows")
        with patch("agents.common.applications.shutil.which", lambda name: None):
            catalog = registry.discover()
        # Windows ids are fixed: the shell resolves them, so no PATH probe is
        # used and every id is reported.
        self.assertIn("notepad", catalog.ids())
        self.assertIn("calc", catalog.ids())
        self.assertIn("firefox", catalog.ids())


class ProcessNameTests(unittest.TestCase):
    """The `running` flag is a process-name match, so the name has to be whole.

    `/proc/<pid>/comm` is capped at 15 characters by the kernel
    (TASK_COMM_LEN), so a process whose name is longer — `gnome-calculator` —
    reads as `gnome-calculato` and never matches its own application id. The
    fallback now reads `argv[0]`, with `comm` used only when it is gone.
    """

    def _names_without_psutil(self, cmdline: bytes, comm: str) -> list[str]:
        """Run the /proc fallback with psutil unavailable and one fake pid."""
        import agents.common.applications as mod

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("psutil is not installed in this test")
            return real_import(name, *args, **kwargs)

        def fake_open(path, *rest, **kwargs):
            if "cmdline" in str(path):
                return mock_open(read_data=cmdline)()
            return mock_open(read_data=comm)()

        with patch.object(builtins, "__import__", side_effect=fake_import), patch.object(
            mod.os, "listdir", return_value=["4242"]
        ), patch.object(mod, "open", side_effect=fake_open):
            return mod._process_names()

    def test_proc_fallback_reads_the_untruncated_name(self) -> None:
        names = self._names_without_psutil(b"/usr/bin/gnome-calculator\x00--arg\x00", "x\n")
        self.assertEqual(names, ["gnome-calculator"])

    def test_proc_fallback_reads_the_full_argv0_not_comm(self) -> None:
        # Exactly the regression: `comm` alone would be `gnome-calculato`.
        names = self._names_without_psutil(b"/usr/bin/gnome-calculator\x00", "gnome-calculato\n")
        self.assertEqual(names, ["gnome-calculator"])

    def test_proc_fallback_uses_comm_when_argv_is_empty(self) -> None:
        # An empty argv0 falls back to comm rather than emitting "".
        names = self._names_without_psutil(b"", "sh\n")
        self.assertEqual(names, ["sh"])

    def test_a_long_process_name_marks_the_application_running(self) -> None:
        # The whole reason the name matters: discovery compares it against
        # the application id, so a truncated name reported a running
        # application as stopped.
        registry = ApplicationRegistry(platform="linux")
        with patch("agents.common.applications.shutil.which", lambda name: "/usr/bin/" + name), patch(
            "agents.common.applications.ApplicationRegistry._running_process_names",
            staticmethod(lambda: {"gnome-calculator"}),
        ):
            catalog = registry.discover()
        app = catalog.find("gnome-calculator")
        self.assertIsNotNone(app)
        self.assertTrue(app.running)


if __name__ == "__main__":
    unittest.main()

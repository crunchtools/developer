"""ashigaru-hook: `ashigaru hook` once the Desk is configured, nothing before."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "rootfs/usr/libexec/claude-seat/ashigaru-hook"
EVENT = '{"hook_event_name": "UserPromptSubmit", "prompt": "hi"}'


class AshigaruHookTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # A stand-in ashigaru that records its argv and what it read.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.calls = self.root / "calls"
        stub = bin_dir / "ashigaru"
        stub.write_text(f'#!/bin/sh\necho "$*" >> {self.calls}\ncat >> {self.calls}\n')
        stub.chmod(0o755)
        self.key = self.root / "key"
        self.key.write_text("secret\n")
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    def run_hook(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(HOOK), *args],
            input=EVENT,
            env={"PATH": self.path, "HOME": str(self.root), **env},
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_skipped(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.calls.exists())

    def test_it_runs_ashigaru_hook_with_the_event(self) -> None:
        env = {"ASHIGARU_URL": "https://desk.example", "ASHIGARU_API_KEY_FILE": str(self.key)}
        result = self.run_hook(env, "prompt-submit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text(), f"hook prompt-submit\n{EVENT}")

    def test_a_key_in_the_environment_is_a_key(self) -> None:
        env = {"ASHIGARU_URL": "https://desk.example", "ASHIGARU_API_KEY": "secret"}
        self.assertEqual(self.run_hook(env, "stop").returncode, 0)
        self.assertTrue(self.calls.read_text().startswith("hook stop\n"))

    def test_without_a_url_it_does_nothing(self) -> None:
        self.assert_skipped(self.run_hook({"ASHIGARU_API_KEY_FILE": str(self.key)}, "stop"))

    def test_without_a_key_it_does_nothing(self) -> None:
        self.assert_skipped(self.run_hook({"ASHIGARU_URL": "https://desk.example"}, "stop"))

    def test_a_key_file_not_yet_staged_is_no_key(self) -> None:
        env = {"ASHIGARU_URL": "https://desk.example", "ASHIGARU_API_KEY_FILE": str(self.root / "nope")}
        self.assert_skipped(self.run_hook(env, "stop"))

    def test_configured_without_the_cli_says_so_and_exits_0(self) -> None:
        # A PATH with cat and nothing else, so an ashigaru on this host is not found.
        bare = self.root / "bare"
        bare.mkdir()
        (bare / "cat").symlink_to(shutil.which("cat") or "/bin/cat")
        env = {"ASHIGARU_URL": "https://desk.example", "ASHIGARU_API_KEY": "secret"}
        result = subprocess.run(
            [shutil.which("sh") or "/bin/sh", str(HOOK), "stop"],
            input=EVENT,
            env={"PATH": str(bare), "HOME": str(self.root), **env},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("not installed", result.stderr)

    def test_it_is_executable(self) -> None:
        self.assertTrue(HOOK.stat().st_mode & 0o111)

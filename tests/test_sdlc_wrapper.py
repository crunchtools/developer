"""The `sdlc` wrapper: it runs the python-sdlc the repo pins, or it refuses."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "rootfs/usr/local/bin/sdlc"

CONFIG = """\
repos:
  - repo: https://github.com/crunchtools/gourmand
    rev: v9.9.9
    hooks:
      - id: gourmand
  - repo: https://github.com/crunchtools/python-sdlc
    rev: {rev}
    hooks:
      - id: sdlc-commit
"""


class WrapperTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        # A stand-in python-sdlc that `git clone --branch <rev>` can reach.
        self.origin = self.root / "python-sdlc"
        (self.origin / "bin").mkdir(parents=True)
        launcher = self.origin / "bin/sdlc"
        launcher.write_text('#!/bin/bash\necho "launcher got: $*"\n')
        launcher.chmod(0o755)
        env = {
            "HOME": str(self.root),
            "PATH": os.environ["PATH"],
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"], ["tag", "v1.2.3"]):
            subprocess.run(["git", *args], cwd=self.origin, check=True, env=env)
        self.env = {
            "HOME": str(self.root),
            "PATH": os.environ["PATH"],
            "SDLC_WRAPPER_REPO": str(self.origin),
            "SDLC_WRAPPER_CACHE": str(self.root / "cache"),
        }

    def run_wrapper(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(WRAPPER), *args],
            cwd=cwd or self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def write_config(self, rev: str = "v1.2.3") -> None:
        (self.repo / ".pre-commit-config.yaml").write_text(CONFIG.format(rev=rev))

    def test_it_runs_the_rev_the_repo_pins(self) -> None:
        self.write_config()
        result = self.run_wrapper("push", "--flag")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("launcher got: push --flag", result.stdout)
        # The gourmand entry is listed first and must not be mistaken for it.
        self.assertTrue((self.root / "cache/v1.2.3/bin/sdlc").exists())
        self.assertFalse((self.root / "cache/v9.9.9").exists())

    def test_the_second_run_does_not_clone_again(self) -> None:
        self.write_config()
        self.assertIn("fetched python-sdlc", self.run_wrapper("tiers").stderr)
        self.assertNotIn("fetched python-sdlc", self.run_wrapper("tiers").stderr)

    def test_outside_a_repo_it_refuses(self) -> None:
        result = self.run_wrapper("push", cwd=self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not inside a git repository", result.stderr)

    def test_without_a_config_it_refuses(self) -> None:
        result = self.run_wrapper("push")
        self.assertEqual(result.returncode, 2)
        self.assertIn("pins no python-sdlc", result.stderr)

    def test_a_config_without_python_sdlc_refuses(self) -> None:
        (self.repo / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: https://github.com/crunchtools/gourmand\n    rev: v9.9.9\n"
        )
        result = self.run_wrapper("push")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no python-sdlc rev", result.stderr)

    def test_a_rev_that_is_not_a_plain_tag_is_refused(self) -> None:
        self.write_config(rev="$(touch /tmp/pwned)")
        result = self.run_wrapper("push")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a plain tag", result.stderr)
        self.assertFalse(Path("/tmp/pwned").exists())

    def test_a_dot_dot_rev_cannot_escape_the_cache(self) -> None:
        # The rev is a path component, so `..` would run whatever sits beside
        # the cache rather than the pinned tool.
        outside = self.root / "bin"
        outside.mkdir()
        planted = outside / "sdlc"
        planted.write_text('#!/bin/bash\ntouch "$HOME/escaped"\n')
        planted.chmod(0o755)
        for rev in ("..", "."):
            with self.subTest(rev=rev):
                self.write_config(rev=rev)
                result = self.run_wrapper("push")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("not a plain tag", result.stderr)
        self.assertFalse((self.root / "escaped").exists())

    def test_an_unreachable_rev_is_a_could_not_run(self) -> None:
        self.write_config(rev="v0.0.0-nope")
        result = self.run_wrapper("push")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot clone python-sdlc", result.stderr)

"""claude-skills-sync, run against throwaway git repositories (#42)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SYNC = Path(__file__).resolve().parent.parent / "rootfs/usr/local/bin/claude-skills-sync"


def status_of(repo: Path) -> dict[str, str]:
    line = (repo / ".git/skills-sync.status").read_text()
    assert line.startswith("SKILLSSYNC "), line
    assert line.endswith("\n") and line.count("\n") == 1, line
    return dict(field.split("=", 1) for field in line.split()[1:])


class SkillsSyncCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1"}
        self.upstream = self.root / "upstream"
        self.add_skill(self.upstream, "alpha", init=True)
        self.repo = self.root / "josui-skills"
        self.git(self.root, "clone", "-q", str(self.upstream), str(self.repo))
        self.claude = self.home / ".claude"

    def git(self, cwd: Path, *args: str) -> str:
        identity = ("-c", "user.name=t", "-c", "user.email=t@example.com")
        done = subprocess.run(
            ["git", *identity, *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def add_skill(self, repo: Path, name: str, *, init: bool = False) -> None:
        (repo / "skills" / name).mkdir(parents=True)
        (repo / "skills" / name / "SKILL.md").write_text(f"{name}\n")
        if init:
            self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", name)

    def sync(self, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SYNC), "--link", str(self.claude), *extra, str(self.repo)],
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def linked(self) -> list[str]:
        return sorted(p.name for p in (self.claude / "skills").iterdir())


class SkillsSyncTest(SkillsSyncCase):
    def test_a_merge_upstream_is_fast_forwarded_and_linked(self) -> None:
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(self.linked(), ["alpha"])
        self.add_skill(self.upstream, "beta")

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.git(self.repo, "rev-parse", "HEAD"), self.git(self.upstream, "rev-parse", "HEAD")
        )
        self.assertEqual(self.linked(), ["alpha", "beta"])
        status = status_of(self.repo)
        self.assertEqual(status["state"], "current")
        self.assertEqual(status["drift_since"], "0")
        self.assertEqual(status["head"], self.git(self.upstream, "rev-parse", "HEAD"))
        self.assertGreater(int(status["fetched"]), 0)

    def test_an_existing_link_is_left_in_place_and_a_removed_skill_unlinked(self) -> None:
        self.add_skill(self.upstream, "beta")
        self.sync()
        alpha = (self.claude / "skills/alpha").lstat().st_ino
        shutil.rmtree(self.upstream / "skills/beta")
        self.git(self.upstream, "commit", "-qam", "drop beta")

        self.sync()

        self.assertEqual(self.linked(), ["alpha"])
        self.assertEqual((self.claude / "skills/alpha").lstat().st_ino, alpha)

    def test_a_diverged_checkout_is_left_alone_and_its_drift_dated(self) -> None:
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "local")
        local = self.git(self.repo, "rev-parse", "HEAD")
        self.add_skill(self.upstream, "beta")

        first = self.sync()
        since = status_of(self.repo)["drift_since"]
        second = self.sync()

        self.assertIn("could not be fast-forwarded", first.stderr)
        self.assertIn("could not be fast-forwarded", second.stderr)
        # One line a pass, with git's reason, and none of its hint block.
        self.assertEqual(len(first.stderr.strip().splitlines()), 1, first.stderr)
        self.assertNotIn("hint:", first.stderr)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), local)
        self.assertEqual(status_of(self.repo)["state"], "diverged")
        self.assertGreater(int(since), 0)
        self.assertEqual(status_of(self.repo)["drift_since"], since)

    def test_a_feature_branch_is_moved_back_to_main(self) -> None:
        self.git(self.repo, "checkout", "-qb", "fix/old")
        self.add_skill(self.upstream, "beta")
        self.sync()
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(status_of(self.repo)["state"], "current")

    def test_a_failed_fetch_keeps_the_last_good_fetch_time(self) -> None:
        self.sync()
        fetched = status_of(self.repo)["fetched"]
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "gone"))

        result = self.sync()

        self.assertEqual(result.returncode, 0)
        self.assertIn("fetch failed", result.stderr)
        self.assertEqual(status_of(self.repo)["fetched"], fetched)

    def test_a_failed_fetch_is_stale_not_current_and_dated(self) -> None:
        self.sync()
        good = status_of(self.repo)
        self.assertEqual(good["state"], "current")
        self.assertEqual(good["drift_since"], "0")
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "gone"))

        # The stale origin/main still equals HEAD; that must not read as current.
        self.sync()
        first = status_of(self.repo)
        self.sync()
        second = status_of(self.repo)

        self.assertEqual(first["head"], first["origin"])
        self.assertEqual(first["state"], "stale")
        self.assertGreater(int(first["drift_since"]), 0)
        self.assertEqual(second["state"], "stale")
        self.assertEqual(second["drift_since"], first["drift_since"])
        self.assertEqual(second["fetched"], good["fetched"])

    def test_a_fetch_that_works_again_is_current_and_clears_the_drift(self) -> None:
        self.sync()
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "gone"))
        self.sync()
        self.assertEqual(status_of(self.repo)["state"], "stale")
        self.git(self.repo, "remote", "set-url", "origin", str(self.upstream))

        self.sync()

        after = status_of(self.repo)
        self.assertEqual(after["state"], "current")
        self.assertEqual(after["drift_since"], "0")
        self.assertGreater(int(after["fetched"]), 0)

    def test_an_empty_directory_is_cloned(self) -> None:
        shutil.rmtree(self.repo)
        self.repo.mkdir()
        result = self.sync(env={**self.env, "JOSUI_SKILLS_URL": str(self.upstream)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.linked(), ["alpha"])
        self.assertEqual(status_of(self.repo)["state"], "current")

    def test_bad_arguments_are_refused(self) -> None:
        for args in ([], ["--link"], ["--bogus", "x"], ["a", "b"]):
            done = subprocess.run(["bash", str(SYNC), *args], env=self.env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 2, args)


if __name__ == "__main__":
    unittest.main()

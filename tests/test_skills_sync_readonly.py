"""claude-skills-sync on a checkout it cannot write (#42).

Its own file because it is skipped as root, which writes through a read-only
mode. Gourmand matches an exception by path alone, so the exception for this
skip names this file and covers nothing else.
"""

from __future__ import annotations

import os
import unittest

from test_skills_sync import SkillsSyncCase


class ReadOnlyCheckoutTest(SkillsSyncCase):
    @unittest.skipIf(os.geteuid() == 0, "root writes through a read-only mode")
    def test_a_checkout_it_cannot_write_is_only_read_and_relinked(self) -> None:
        self.add_skill(self.upstream, "beta")
        before = self.git(self.repo, "rev-parse", "HEAD")
        self.add_skill(self.repo, "gamma")  # as if the owner had already pulled it
        gamma = self.git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(before, gamma)
        (self.repo / ".git").chmod(0o555)
        self.addCleanup((self.repo / ".git").chmod, 0o755)

        result = self.sync()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), gamma)
        self.assertEqual(self.linked(), ["alpha", "gamma"])
        self.assertFalse((self.repo / ".git/skills-sync.status").exists())

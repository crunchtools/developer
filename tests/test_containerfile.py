"""The image says which release it is."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTAINERFILE = ROOT / "Containerfile"


class LabelTest(unittest.TestCase):
    # The base image sets org.opencontainers.image.version to its own release
    # (podman 6.1.3), which the image inherited and reported as its version.
    def test_the_oci_version_label_is_this_release(self) -> None:
        labels = dict(re.findall(r'^LABEL ([\w.]+)="([^"]*)"', CONTAINERFILE.read_text(), re.M))
        self.assertRegex(labels["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(labels.get("org.opencontainers.image.version"), labels["version"])


class AshigaruPinTest(unittest.TestCase):
    # The release asset can be replaced (rpm.yml uploads with --clobber), so
    # the build installs only an RPM whose hash is pinned here.
    def test_the_rpm_is_pinned_by_sha256(self) -> None:
        args = dict(re.findall(r"^ARG (ASHIGARU_\w+)=(\S*)$", CONTAINERFILE.read_text(), re.M))
        self.assertRegex(args["ASHIGARU_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(args["ASHIGARU_SHA256"], r"^[0-9a-f]{64}$")
        self.assertIn("sha256sum -c --strict", CONTAINERFILE.read_text())


class AshigaruSkillTest(unittest.TestCase):
    # HOME is a volume, so the skill reaches Claude Code only through the
    # managed skills folder; the image build and container.yml check the link
    # resolves to the RPM's SKILL.md.
    def test_the_rpm_skill_is_linked_into_managed_skills(self) -> None:
        self.assertIn(
            "ln -s /usr/share/ashigaru/skills/ashigaru /etc/claude-code/.claude/skills/ashigaru",
            CONTAINERFILE.read_text(),
        )

    def test_the_build_checks_the_installed_version(self) -> None:
        self.assertIn(
            'rpm -q --qf \'%{VERSION}\' ashigaru)" = "$ASHIGARU_VERSION"', CONTAINERFILE.read_text()
        )

    def test_the_image_points_at_the_desk_but_carries_no_key(self) -> None:
        text = CONTAINERFILE.read_text()
        self.assertRegex(text, r"(?m)^\s+ASHIGARU_URL=https://ashigaru\.crunchtools\.com \\$")
        self.assertNotIn("ASHIGARU_API_KEY=", text)


class ImageCopyTmpDirTest(unittest.TestCase):
    # The units cap /var/tmp at 1 GB of tmpfs, too small for a large
    # `podman build`; buildah stages under the storage volume instead.
    def test_buildah_stages_under_the_storage_volume(self) -> None:
        conf = ROOT / "rootfs/etc/containers/containers.conf.d/50-image-copy-tmp-dir.conf"
        self.assertRegex(conf.read_text(), r'(?ms)^\[engine\]$.*^image_copy_tmp_dir = "storage"$')


class OldNamesTest(unittest.TestCase):
    # The image was `developer` (#41): for one release the old command and
    # hook paths are links to the new ones, so a unit or a person using them
    # still lands on the same program.
    LINKS = {
        "/usr/local/bin/developer-entrypoint": "claude-entrypoint",
        "/usr/local/bin/developer-worker-claude": "claude-worker",
        "/usr/local/bin/developer-skills-sync": "claude-skills-sync",
        "/usr/libexec/developer": "claude-seat",
    }

    def test_the_old_names_link_to_the_new(self) -> None:
        text = CONTAINERFILE.read_text()
        for old, new in self.LINKS.items():
            self.assertIn(f"ln -s {new} {old}", text)
            self.assertTrue((ROOT / "rootfs" / Path(old).parent.relative_to("/") / new).exists(), new)

    def test_the_old_paths_are_not_shipped_as_files(self) -> None:
        for old in self.LINKS:
            self.assertFalse((ROOT / "rootfs" / old.lstrip("/")).exists(), old)

    # Every managed-settings fragment is loaded: a second copy of the seat
    # hooks under the old name would run each of them twice.
    def test_only_the_new_seat_settings_are_shipped(self) -> None:
        managed = ROOT / "rootfs/etc/claude-code/managed-settings.d"
        self.assertTrue((managed / "40-claude-seat.json").is_file())
        self.assertFalse((managed / "40-developer-seat.json").exists())

    def test_the_entrypoint_finds_skills_sync_beside_its_real_path(self) -> None:
        entrypoint = (ROOT / "rootfs/usr/local/bin/claude-entrypoint").read_text()
        self.assertIn('"$(dirname "$(readlink -f "$0")")/claude-skills-sync"', entrypoint)

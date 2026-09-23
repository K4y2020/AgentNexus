"""Offline regression checks for installer compatibility across the rename.

Also runs with stdlib unittest on hosts without the project's pytest environment.
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh")


@unittest.skipUnless(SH, "POSIX sh is required")
class InstallerRenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="agentnexus-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile = self.directory / ".profile"
        self.env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.directory),
            "TMPDIR": str(self.directory),
            "TEST_PROFILE": str(self.profile),
        }
        self.installer = self.directory / "install.sh"
        installer = (ROOT / "scripts/install_oss.sh").read_text(encoding="utf-8")
        self.assertEqual(installer.count('\nmain "$@"'), 1)
        self.installer.write_text(installer.replace('\nmain "$@"', ""), encoding="utf-8")
        self.uninstaller = self.directory / "uninstall.sh"
        uninstaller = (ROOT / "scripts/uninstall_oss.sh").read_text(encoding="utf-8")
        self.assertEqual(uninstaller.count("\nif ! has_shell_install_signal; then"), 1)
        self.uninstaller.write_text(
            uninstaller.split("\nif ! has_shell_install_signal; then", 1)[0], encoding="utf-8"
        )

    def shell(self, library: Path, program: str, **env: str) -> subprocess.CompletedProcess[str]:
        assert SH
        return subprocess.run(
            [SH, "-c", f". {shlex.quote(str(library))}\n{program}"],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=15,
        )

    @staticmethod
    def block(name: str, directory: str = "/test/bin") -> str:
        return (
            f"# >>> {name} installer >>>\n"
            f'export PATH="{directory}:$PATH"\n'
            f"# <<< {name} installer <<<\n"
        )

    def install_path(self) -> subprocess.CompletedProcess[str]:
        return self.shell(
            self.installer,
            'init_style\n'
            'pick_profile() { printf "%s\\n" "$TEST_PROFILE"; }\n'
            'prompt_yes_no() { return 0; }\n'
            'maybe_add_bin_to_path /test/bin\n',
        )

    def test_installer_writes_current_markers(self) -> None:
        result = self.install_path()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.block("AgentNexus"), self.profile.read_text())

    def test_installer_recognizes_both_existing_blocks(self) -> None:
        for name in ("AgentNexus", "Omnigent"):
            with self.subTest(name=name):
                original = "# user settings\n" + self.block(name)
                self.profile.write_text(original)
                result = self.install_path()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.profile.read_text(), original)

    def test_installer_refuses_conflicting_legacy_block(self) -> None:
        original = self.block("Omnigent", "/old/bin")
        self.profile.write_text(original)
        result = self.install_path()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.profile.read_text(), original)

    def test_uninstaller_removes_both_blocks_and_preserves_user_content(self) -> None:
        original = "before\n" + self.block("Omnigent") + "between\n" + self.block("AgentNexus") + "after\n"
        self.profile.write_text(original)
        result = self.shell(self.uninstaller, "DRY_RUN=false\ncleanup_profiles\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile.read_text(), "before\nbetween\nafter\n")
        backups = list(self.directory.glob(".profile.agentnexus.bak.*"))
        self.assertEqual(len(backups), 2)
        self.assertIn(original, [path.read_text() for path in backups])

    def test_uninstaller_leaves_incomplete_blocks(self) -> None:
        original = "before\n# >>> Omnigent installer >>>\nuser content\n"
        self.profile.write_text(original)
        result = self.shell(self.uninstaller, "DRY_RUN=false\ncleanup_profiles\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.profile.read_text(), original)

    def test_uninstaller_checks_legacy_block_hash(self) -> None:
        block = self.block("Omnigent")
        self.profile.write_text(block)
        mismatch = self.shell(
            self.uninstaller, 'DRY_RUN=false\nremove_profile_block "$TEST_PROFILE" bad-hash\n'
        )
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertEqual(self.profile.read_text(), block)
        digest = hashlib.sha256(block.encode()).hexdigest()
        match = self.shell(
            self.uninstaller,
            f'DRY_RUN=false\nremove_profile_block "$TEST_PROFILE" {digest}\n',
        )
        self.assertEqual(match.returncode, 0, match.stderr)
        self.assertEqual(self.profile.read_text(), "")

    def test_data_dir_resolution(self) -> None:
        custom = self.directory / "custom-data"
        for overrides, expected in (
            ({}, self.directory / ".agentnexus"),
            ({"AGENTNEXUS_DATA_DIR": ""}, self.directory / ".agentnexus"),
            ({"AGENTNEXUS_DATA_DIR": str(custom)}, custom),
        ):
            with self.subTest(overrides=overrides):
                result = self.shell(self.uninstaller, "state_home", **overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(expected))

    def test_nightly_uses_canonical_source_and_env_precedence(self) -> None:
        text = (ROOT / "scripts/update_nightly.sh").read_text(encoding="utf-8")
        library = self.directory / "nightly.sh"
        library.write_text(text.split("# Newest nightly tag:", 1)[0].replace("set -euo pipefail", "set -eu"))
        for overrides, expected in (
            ({}, "https://github.com/K4y2020/AgentNexus|3.12"),
            ({"AGENTNEXUS_REPO": "custom", "AGENTNEXUS_PYTHON_VERSION": "3.13"}, "custom|3.13"),
            ({"AGENTNEXUS_REPO": "", "AGENTNEXUS_PYTHON_VERSION": ""}, "|"),
        ):
            with self.subTest(overrides=overrides):
                result = self.shell(library, 'printf "%s|%s" "$REPO" "$PYTHON_VERSION"', **overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)


if __name__ == "__main__":
    unittest.main()

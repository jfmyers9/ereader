import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTest(unittest.TestCase):
    def test_overlay_backup_and_settings_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "reader with spaces"
            plugin = device / "plugins/tailscale.koplugin"
            (plugin / "bin").mkdir(parents=True)
            (plugin / "main.lua").write_text("old plugin")
            runtime = ("auth.key", "tailscaled.state", "tailscale", "tailscaled")
            for name in runtime:
                (plugin / "bin" / name).write_text(f"preserve {name}")
            (device / "settings.reader.lua").write_text("reader settings")
            backup = installer.install(device, True, root / "backups")
            self.assertEqual((backup / "plugins/tailscale.koplugin/main.lua").read_text(), "old plugin")
            self.assertEqual((backup / "settings.reader.lua").read_text(), "reader settings")
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
            for name in runtime:
                self.assertEqual((plugin / "bin" / name).read_text(), f"preserve {name}")
            for name in installer.FILES:
                self.assertEqual((plugin / name).read_bytes(), (ROOT / "koreader-tailscale" / name).read_bytes())
            settings = device / "settings/tailscale.lua"
            self.assertEqual(settings.read_bytes(), (ROOT / "profiles/userspace-proxy/tailscale.lua").read_bytes())
            settings.write_text("custom settings")
            (plugin / "main.lua").write_text("drift")
            second = installer.install(device, True, root / "backups")
            self.assertNotEqual(backup, second)
            self.assertEqual(settings.read_text(), "custom settings")
            self.assertEqual((second / "settings/tailscale.lua").read_text(), "custom settings")

    def test_kindle_check_install_and_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "kindle/koreader"
            (device / "plugins").mkdir(parents=True)
            (device.parent / "documents").mkdir()
            (device.parent / "kmc").mkdir()
            launcher = device.parent / "documents/KOReader No Framework.sh"
            launcher.write_text("old launcher")
            normal = device.parent / "documents/KOReader.sh"
            normal.write_text("normal launcher")
            backups = root / "backups"
            self.assertIs(installer.install(device, True, backups, kindle=True, check=True), False)
            self.assertFalse(backups.exists())
            self.assertEqual(launcher.read_text(), "old launcher")
            self.assertEqual(list((device / "plugins").iterdir()), [])
            backup = installer.install(device, True, backups, kindle=True)
            self.assertEqual((backup / "kindle/documents" / launcher.name).read_text(), "old launcher")
            self.assertEqual(launcher.read_bytes(), (ROOT / "kindle/documents" / launcher.name).read_bytes())
            self.assertTrue(launcher.stat().st_mode & 0o111)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
            self.assertIsNone(installer.install(device, True, backups, kindle=True))
            self.assertIsNone(installer.install(device, True, backups, kindle=True, check=True))
            after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(len(list(backups.iterdir())), 1)
            self.assertEqual(normal.read_text(), "normal launcher")
            launcher.chmod(0o644)
            self.assertIs(installer.install(device, True, backups, kindle=True, check=True), False)
            installer.install(device, True, backups, kindle=True)
            self.assertTrue(launcher.stat().st_mode & 0o111)

    def test_cli_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "reader"
            command = [sys.executable, str(ROOT / "scripts/install.py"), str(device), "--check"]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            (device / "plugins").mkdir(parents=True)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(list((device / "plugins").iterdir()), [])
            installer.install(device, backup_root=root / "backups")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_kindle_requires_kmc_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "koreader"
            (device / "plugins").mkdir(parents=True)
            (root / "documents").mkdir()
            with self.assertRaisesRegex(ValueError, "KMC"):
                installer.install(device, backup_root=root / "backups", kindle=True)
            self.assertFalse((root / "backups").exists())
            self.assertEqual(list((device / "plugins").iterdir()), [])

    def test_refuse_symlinked_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "reader"
            plugin = device / "plugins/tailscale.koplugin"
            plugin.mkdir(parents=True)
            other = root / "other.lua"
            other.write_text("unrelated")
            (plugin / "main.lua").symlink_to(other)
            with self.assertRaisesRegex(ValueError, "Symlinked"):
                installer.install(device, backup_root=root / "backups")
            self.assertEqual(other.read_text(), "unrelated")
            self.assertFalse((root / "backups").exists())

    def test_fresh_install_without_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "reader"
            (device / "plugins").mkdir(parents=True)
            installer.install(device, backup_root=root / "backups")
            self.assertTrue((device / "plugins/tailscale.koplugin/main.lua").exists())
            self.assertFalse((device / "settings/tailscale.lua").exists())

    def test_reject_invalid_target_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                installer.install(root, backup_root=root / "backups")
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

import importlib.util
from pathlib import Path
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
            second = installer.install(device, True, root / "backups")
            self.assertNotEqual(backup, second)
            self.assertEqual(settings.read_text(), "custom settings")
            self.assertEqual((second / "settings/tailscale.lua").read_text(), "custom settings")

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

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import install, settings


@unittest.skipUnless(shutil.which("luajit"), "Settings tests require host LuaJIT")
class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self.root / "koreader"
        (self.target / "plugins").mkdir(parents=True)

    def assert_lua(self, path, assertions):
        subprocess.run([shutil.which("luajit"), "-e",
                        "local t = dofile(arg[0]); " + assertions, str(path)], check=True, capture_output=True)

    def test_merge_preserves_unmanaged_values_and_types(self):
        path = self.target / "settings.reader.lua"
        path.write_text('''return {
          bookorbit = {username="reader", userkey="test-key", server_url="http://example.test", settings_version=7,
                       auto_sync=true, unknown_setting="keep"},
          device_id="test-identity", lastfile="/books/example.epub",
          private={ [0]="zero", [1]=false, fraction=0.12345678901234567, text="line\\nquote\\\"slash\\\\" },
          footer={extra="preserve"}, style_tweaks={custom=true},
          autowarmth_latitude=12.345,
        }''')
        changes = settings.plan(self.target, "shared")
        self.assertEqual(len(changes), 1)
        self.assertNotIn("test-key", "\n".join(changes[0].keys))
        changes[0].write()
        self.assert_lua(path, '''
          assert(t.bookorbit.username == "reader" and t.bookorbit.userkey == "test-key")
          assert(t.bookorbit.server_url == "http://example.test" and t.bookorbit.settings_version == 7)
          assert(t.bookorbit.unknown_setting == "keep" and t.bookorbit.auto_sync == false)
          assert(t.device_id == "test-identity" and t.lastfile == "/books/example.epub")
          assert(t.private[0] == "zero" and t.private[1] == false)
          assert(t.private.fraction == 0.12345678901234567)
          assert(t.private.text == "line\\nquote\\\"slash\\\\")
          assert(t.footer.extra == "preserve" and t.style_tweaks.custom == true)
          assert(t.autowarmth_latitude == 12.345 and t.cre_font == "Bookerly")
          assert(t.copt_h_page_margins[1] == 30 and t.footer.order[0] == "off")
        ''')
        self.assertEqual(settings.plan(self.target, "shared"), [])

    def test_profile_backup_drift_and_idempotency(self):
        (self.target / "settings").mkdir()
        gestures = self.target / "settings/gestures.lua"
        old = b'return {gesture_reader={hold_bottom_left_corner={toggle_tailscale_network=true}},custom={keep=true}}'
        gestures.write_bytes(old)
        before = {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        backups = self.root / "backups"
        self.assertIs(install.install(self.target, backup_root=backups, settings_profile="kindle", check=True), False)
        self.assertFalse(backups.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.target.rglob("*") if p.is_file()})
        backup = install.install(self.target, backup_root=backups, settings_profile="kindle", userspace_proxy=True)
        self.assertEqual((backup / "settings/gestures.lua").read_bytes(), old)
        manifest = json.loads((backup / "settings-profile-files.json").read_text())
        self.assertTrue(manifest["settings/gestures.lua"]["existed"])
        self.assertFalse(manifest["settings.reader.lua"]["existed"])
        self.assert_lua(gestures, '''
          assert(t.gesture_reader.hold_bottom_left_corner.toggle_tailscale_vpn)
          assert(t.gesture_reader.hold_bottom_left_corner.toggle_tailscale_network == nil)
          assert(t.gesture_fm.hold_bottom_left_corner.toggle_tailscale_vpn)
          assert(t.gesture_reader.hold_bottom_right_corner.bookorbit_sync_now)
          assert(t.custom.keep)
        ''')
        self.assert_lua(self.target / "settings.reader.lua", '''
          assert(t.home_dir == "/mnt/us/Books" and t.auto_suspend_timeout_seconds == 900)
          assert(t.bookorbit.settings_version == 1)
        ''')
        self.assert_lua(self.target / "settings/tailscale.lua", "assert(t.force_userspace and t.auto_http_proxy)")
        mtimes = {p: p.stat().st_mtime_ns for p in self.target.rglob("*") if p.is_file()}
        self.assertIsNone(install.install(self.target, backup_root=backups, settings_profile="kindle"))
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in self.target.rglob("*") if p.is_file()})
        self.assertEqual(len(list(backups.iterdir())), 1)

    def test_invalid_or_executable_settings_fail_before_any_write(self):
        path = self.target / "settings.reader.lua"
        for source in ("not lua!", "os.execute('false'); return {}", "while true do end",
                       "return { f=function() end }", "local t={}; t.self=t; return t"):
            path.write_text(source)
            with self.assertRaisesRegex(ValueError, "Cannot safely merge"):
                install.install(self.target, backup_root=self.root / "backups", settings_profile="shared")
            self.assertEqual(path.read_text(), source)
            self.assertFalse((self.root / "backups").exists())
            self.assertEqual(list((self.target / "plugins").iterdir()), [])

    def test_refuse_missing_primary_with_old_backup(self):
        (self.target / "settings.reader.lua.old").write_text('return {device_id="keep"}')
        with self.assertRaisesRegex(ValueError, "Restore"):
            settings.plan(self.target, "shared")

    def test_refuse_symlink_and_concurrent_changes(self):
        path = self.target / "settings.reader.lua"
        external = self.root / "external.lua"
        external.write_text("return {}")
        path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            settings.plan(self.target, "shared")
        path.unlink()
        change = settings.plan(self.target, "shared")[0]
        path.write_text("return {concurrent=true}")
        with self.assertRaisesRegex(ValueError, "changed since planning"):
            change.write()
        self.assertEqual(path.read_text(), "return {concurrent=true}")

    def test_missing_luajit_fails_clearly(self):
        with patch.object(shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "LuaJIT"):
                settings.plan(self.target, "shared")


if __name__ == "__main__":
    unittest.main()

import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import bookorbit, install


class BookOrbitTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name, value in (("CACHE", self.root / "cache"), ("LOCK", self.root / "bookorbit.json")):
            patcher = patch.object(bookorbit, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.files = {"main.lua": b"plugin", "_meta.lua": b"metadata", "assets/icon.svg": b"icon"}
        self.lock = {"schema": 1, "release": "3.2.0", "revision": "a" * 40,
                     "sha256": bookorbit.content_hash(self.files)}
        bookorbit.LOCK.write_text(json.dumps(self.lock))

    def test_fetch_verifies_before_caching_and_reuses_offline(self):
        with patch.object(bookorbit, "download", return_value=(self.lock["revision"], self.files)) as download:
            bookorbit.fetch()
            bookorbit.fetch()
            download.assert_called_once_with(self.lock["revision"])
        self.assertEqual(len(bookorbit.prepared_files()), 3)
        self.assertEqual(json.loads(bookorbit.LOCK.read_text()), self.lock)
        directory = bookorbit.CACHE / self.lock["revision"] / "bookorbit.koplugin"
        (directory / "main.lua").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            bookorbit.prepared_files()

    def test_mismatch_is_not_cached(self):
        with patch.object(bookorbit, "download", return_value=(self.lock["revision"], {"main.lua": b"bad"})):
            with self.assertRaisesRegex(ValueError, "does not match"):
                bookorbit.fetch()
        self.assertFalse((bookorbit.CACHE / self.lock["revision"]).exists())

    def test_checksum_covers_names_and_bytes_not_order(self):
        self.assertEqual(bookorbit.content_hash(self.files), bookorbit.content_hash(dict(reversed(list(self.files.items())))))
        self.assertNotEqual(bookorbit.content_hash({"a": b"x"}), bookorbit.content_hash({"b": b"x"}))
        self.assertNotEqual(bookorbit.content_hash({"a": b"x"}), bookorbit.content_hash({"a": b"y"}))

    def test_reject_unsafe_or_preconfigured_archives(self):
        for unsafe in ("../outside", "/absolute", "bookorbit_provision.lua", "link", "main.lua"):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w") as archive:
                for name in ("main.lua", "_meta.lua", unsafe):
                    entry = tarfile.TarInfo(name)
                    if name == "link":
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = "/outside"
                        archive.addfile(entry)
                    else:
                        entry.size = 1
                        archive.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaises(ValueError, msg=unsafe):
                bookorbit.archive_files(data.getvalue())

    def test_install_preserves_private_state_and_is_idempotent(self):
        bookorbit.store_files(self.lock["revision"], self.files)
        target = self.root / "koreader"
        plugin = target / "plugins/bookorbit.koplugin"
        plugin.mkdir(parents=True)
        (plugin / "main.lua").write_text("old plugin")
        (plugin / "local-file.txt").write_text("leave alone")
        (target / "settings").mkdir()
        private = (target / "settings.reader.lua", target / "settings/bookorbit_sync_state.lua")
        for path in private:
            path.write_text("private device-owned state")
        backups = self.root / "backups"
        self.assertIs(install.install(target, backup_root=backups, bookorbit=True, check=True), False)
        self.assertFalse(backups.exists())
        self.assertEqual((plugin / "main.lua").read_text(), "old plugin")
        backup = install.install(target, backup_root=backups, bookorbit=True)
        self.assertEqual((backup / "plugins/bookorbit.koplugin/main.lua").read_text(), "old plugin")
        for path in private:
            self.assertEqual(path.read_text(), "private device-owned state")
            self.assertEqual((backup / path.relative_to(target)).read_bytes(), path.read_bytes())
        self.assertEqual((plugin / "local-file.txt").read_text(), "leave alone")
        before = {p: p.stat().st_mtime_ns for p in target.rglob("*") if p.is_file()}
        self.assertIsNone(install.install(target, backup_root=backups, bookorbit=True))
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in target.rglob("*") if p.is_file()})
        self.assertEqual(len(list(backups.iterdir())), 1)
        (plugin / bookorbit.PROVISION).write_text("pending private provisioning")
        with self.assertRaisesRegex(ValueError, "provisioning"):
            install.install(target, backup_root=backups, bookorbit=True)


if __name__ == "__main__":
    unittest.main()

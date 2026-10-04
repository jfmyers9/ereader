import gzip
import io
import json
from pathlib import Path
import shutil
import runpy
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import dictionaries as d


class DictionariesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for key, value in (("CACHE", self.root / "cache"), ("LOCK", self.root / "lock.json")):
            patcher = patch.object(d, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.root / "device/koreader"
        (self.target / "plugins").mkdir(parents=True)
        self.backups = self.root / "backups"
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.files = {
            "test.ifo": b"StarDict's dict ifo file\nversion=3.0.0\nbookname=Test\nwordcount=1\nidxfilesize=10\n",
            "test.idx": b"a\0" + b"\0" * 7 + b"\1",
            "test.dict.dz": gzip.compress(b"x"),
        }
        self.pin = {"id": "test", "name": "Test", "language": "en-en",
                    "archive": "test.tar.gz", "prefix": "test/", "members": list(self.files),
                    "url": "https://example.org/test.tar.gz"}
        self.make_archive()

    def make_archive(self, extra=None, zipped=False):
        self.pin["archive"] = "test.zip" if zipped else "test.tar.gz"
        self.pin["prefix"] = "" if zipped else "test/"
        path = self.downloads / self.pin["archive"]
        if zipped:
            with zipfile.ZipFile(path, "w") as archive:
                for name, content in self.files.items():
                    archive.writestr(name, content)
                if extra:
                    archive.writestr(extra, b"x")
        else:
            with tarfile.open(path, "w:gz") as archive:
                for name, content in self.files.items():
                    info = tarfile.TarInfo("test/" + name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
                if extra:
                    if isinstance(extra, str):
                        extra = tarfile.TarInfo(extra)
                    archive.addfile(extra, io.BytesIO(b""))
        self.pin["sha256"] = d.digest(path)
        d.LOCK.write_text(json.dumps({"schema": 1, "dictionaries": [self.pin]}))

    def install(self, **kwargs):
        return d.install(self.target, backup_root=self.backups, preferences=False, **kwargs)

    def test_import_cache_and_corruption(self):
        d.fetch(self.downloads)
        with patch.object(d.subprocess, "run") as run:
            d.fetch()
            run.assert_not_called()
        d.archive_path(self.pin).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.install()
        self.assertFalse((self.target / "data").exists())

    def test_download_uses_https_retries_and_verifies(self):
        def download(command, **kwargs):
            self.assertIn("--retry", command)
            self.assertIn("=https", command)
            shutil.copyfile(self.downloads / self.pin["archive"], command[command.index("--output") + 1])
        with patch.object(d.subprocess, "run", side_effect=download) as run:
            d.fetch()
            self.assertEqual(run.call_count, 1)
        self.assertEqual(d.digest(d.archive_path(self.pin)), self.pin["sha256"])

    def test_bad_download_is_not_promoted(self):
        (self.downloads / self.pin["archive"]).write_bytes(b"not the pin")
        with self.assertRaisesRegex(ValueError, "checksum"):
            d.fetch(self.downloads)
        self.assertFalse(d.archive_path(self.pin).exists())
        self.assertFalse(list(d.CACHE.rglob("*.part")))

    def test_check_install_idempotence_and_backup(self):
        d.fetch(self.downloads)
        self.assertIs(self.install(check=True), False)
        self.assertFalse((self.target / "data").exists())
        self.assertFalse(self.backups.exists())
        existing = self.target / "data/dict/existing.dict"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"keep")
        backup = self.install()
        ifo = self.target / "data/dict/test/test.ifo"
        self.assertIn("lang=en-en", ifo.read_text())
        before = ifo.stat().st_mtime_ns
        self.assertIsNone(self.install())
        self.assertEqual(before, ifo.stat().st_mtime_ns)
        self.assertEqual(len(list(self.backups.iterdir())), 1)
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertFalse(json.loads((backup / "dictionary-files.json").read_text())["data/dict/test/test.ifo"]["existed"])
        ifo.write_bytes(b"modified")
        backup = self.install()
        self.assertEqual((backup / "data/dict/test/test.ifo").read_bytes(), b"modified")

    def test_index_cache_invalidated_only_when_index_changes(self):
        d.fetch(self.downloads)
        self.install()
        directory = self.target / "data/dict/test"
        cache = directory / "test.idx.oft"
        cache.write_bytes(b"cache")
        self.assertIsNone(self.install())
        self.assertTrue(cache.exists())
        (directory / "test.idx").write_bytes(b"stale")
        backup = self.install()
        self.assertFalse(cache.exists())
        self.assertEqual((backup / "data/dict/test/test.idx.oft").read_bytes(), b"cache")

    def test_zip_and_unsafe_archives(self):
        self.make_archive(zipped=True)
        d.fetch(self.downloads)
        self.install()
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in ("../outside", "/absolute", link):
            self.make_archive(extra, zipped=True)
            d.fetch(self.downloads)
            with self.assertRaises(ValueError):
                self.install()
        link = tarfile.TarInfo("test/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/outside"
        for extra in ("../outside", "/absolute", "test/test.ifo", link):
            self.make_archive(extra)
            d.fetch(self.downloads)
            with self.assertRaises(ValueError):
                self.install()

    def test_symlink_destination_rejected(self):
        d.fetch(self.downloads)
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "data").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_combined_cli_preflights_before_plugin_writes(self):
        d.fetch(self.downloads)
        d.archive_path(self.pin).write_bytes(b"corrupt")
        with patch("sys.argv", ["install.py", str(self.target), "--dictionaries"]):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(d.ROOT / "scripts/install.py"), run_name="__main__")
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(list((self.target / "plugins").iterdir()), [])

    def test_combined_cli_checks_dictionaries(self):
        with patch.object(d, "install", return_value=False) as install:
            with patch("sys.argv", ["install.py", str(self.target), "--dictionaries", "--check"]):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(d.ROOT / "scripts/install.py"), run_name="__main__")
        self.assertEqual(result.exception.code, 1)
        install.assert_called_once_with(self.target, check=True)
        self.assertEqual(list((self.target / "plugins").iterdir()), [])

    @unittest.skipUnless(shutil.which("luajit"), "LuaJIT required")
    def test_preferences_preserve_other_settings_and_are_idempotent(self):
        d.fetch(self.downloads)
        path = self.target / "settings.reader.lua"
        before = b'return { private_value = "keep", dicts_disabled = { custom = true } }\n'
        path.write_bytes(before)
        backup = d.install(self.target, backup_root=self.backups)
        self.assertEqual((backup / path.name).read_bytes(), before)
        self.assertIn(b"keep", path.read_bytes())
        self.assertIn(b"English (ereader)", path.read_bytes())
        self.assertNotIn(b"font_size", path.read_bytes())
        self.assertIsNone(d.install(self.target, backup_root=self.backups))


if __name__ == "__main__":
    unittest.main()

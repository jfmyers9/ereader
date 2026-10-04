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


class DictionaryFixture(unittest.TestCase):
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


class DictionariesTest(DictionaryFixture):
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


class CrossPointDictionariesTest(DictionaryFixture):
    def setUp(self):
        super().setUp()
        self.target = self.root / "device/sd"
        (self.target / ".crosspoint").mkdir(parents=True)
        self.directory = self.target / "dictionaries/test"

    def install(self, **kwargs):
        return d.install(self.target, backup_root=self.backups, platform="crosspoint", **kwargs)

    def test_layout_preservation_preferences_and_idempotence(self):
        d.fetch(self.downloads)
        settings = self.target / ".crosspoint/settings.json"
        settings.write_bytes(b'{"dictionary": "custom", "private": "keep"}')
        lua = self.target / "settings.reader.lua"
        lua.write_bytes(b"not Lua; must not be evaluated")
        other = self.target / "dictionaries/custom/custom.idx"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"keep")
        with patch.object(d.settings, "plan_files", side_effect=AssertionError("No preference merge")), \
                patch.object(d.subprocess, "run", side_effect=AssertionError("No LuaJIT or downloads")):
            self.assertIs(self.install(check=True), False)
            self.assertFalse(self.directory.exists())
            self.assertFalse(self.backups.exists())
            backup = self.install()
            self.assertIsNone(self.install(check=True))
            before = {p.name: p.stat().st_mtime_ns for p in self.directory.iterdir()}
            self.assertIsNone(self.install())
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in self.directory.iterdir()})
        self.assertEqual((self.directory / "test.idx").read_bytes(), self.files["test.idx"])
        self.assertEqual((self.directory / "test.dict.dz").read_bytes(), self.files["test.dict.dz"])
        self.assertTrue((self.directory / "test.ifo").is_file())
        self.assertEqual(other.read_bytes(), b"keep")
        self.assertEqual(settings.read_bytes(), b'{"dictionary": "custom", "private": "keep"}')
        self.assertEqual(lua.read_bytes(), b"not Lua; must not be evaluated")
        self.assertFalse((self.target / "data").exists())
        self.assertEqual(len(list(self.backups.iterdir())), 1)
        manifest = json.loads((backup / "dictionary-files.json").read_text())
        self.assertFalse(manifest["dictionaries/test/test.idx"]["existed"])
        (self.directory / "test.ifo").write_bytes(b"modified")
        backup = self.install()
        self.assertEqual((backup / "dictionaries/test/test.ifo").read_bytes(), b"modified")

    def test_index_and_synonym_caches(self):
        self.files["test.syn"] = b"alias\0" + b"\0" * 4
        self.pin["members"] = list(self.files)
        self.make_archive()
        d.fetch(self.downloads)
        self.install()
        qidx = self.directory / "test.qidx"
        sidx = self.directory / "test.sidx"
        for changed, invalidated in (
                ("test.ifo", ()), ("test.dict.dz", ()),
                ("test.syn", ("test.sidx",)),
                ("test.idx", ("test.qidx", "test.sidx"))):
            with self.subTest(changed=changed):
                qidx.write_bytes(b"quick cache")
                sidx.write_bytes(b"synonym cache")
                self.assertIsNone(self.install())
                # Same-size edits still invalidate CrossPoint's size-keyed caches.
                (self.directory / changed).write_bytes(b"!" * len(self.files[changed]))
                self.assertIs(self.install(check=True), False)
                self.assertEqual(qidx.read_bytes(), b"quick cache")
                self.assertEqual(sidx.read_bytes(), b"synonym cache")
                backup = self.install()
                for cache in (qidx, sidx):
                    if cache.name in invalidated:
                        self.assertFalse(cache.exists())
                        self.assertTrue((backup / "dictionaries/test" / cache.name).is_file())
                    else:
                        self.assertTrue(cache.exists())
        # Both changed indexes may schedule the same sidx invalidation only once.
        qidx.write_bytes(b"quick cache")
        sidx.write_bytes(b"synonym cache")
        for name in ("test.idx", "test.syn"):
            (self.directory / name).write_bytes(b"stale")
        self.install()
        self.assertFalse(qidx.exists())
        self.assertFalse(sidx.exists())

    def test_requires_existing_sd_root_and_real_marker(self):
        d.fetch(self.downloads)
        marker = self.target / ".crosspoint"
        marker.rmdir()
        for target in (self.target, self.root / "missing"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                d.install(target, platform="crosspoint", backup_root=self.backups)
        self.assertFalse((self.root / "missing").exists())
        marker.write_bytes(b"not a directory")
        with self.assertRaises(ValueError):
            self.install()
        marker.unlink()
        outside = self.root / "outside"
        outside.mkdir()
        marker.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.backups.exists())

    def test_interrupted_index_replacement_cannot_leave_stale_caches(self):
        d.fetch(self.downloads)
        self.install()
        index = self.directory / "test.idx"
        index.write_bytes(b"x" * len(self.files["test.idx"]))
        caches = [self.directory / "test.qidx", self.directory / "test.sidx"]
        for cache in caches:
            cache.write_bytes(b"old offsets")
        replace = d.os.replace

        def interrupted(source, destination):
            replace(source, destination)
            if destination == index:
                raise OSError("interrupted after replacing index")

        with patch.object(d.os, "replace", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.install()
        self.assertEqual(index.read_bytes(), self.files["test.idx"])
        self.assertTrue(all(not cache.exists() for cache in caches))
        self.assertIsNone(self.install())

    def test_symlinked_sd_root_and_parent_rejected(self):
        d.fetch(self.downloads)
        link = self.root / "linked-sd"
        link.symlink_to(self.target, target_is_directory=True)
        parent = self.root / "linked-parent"
        parent.symlink_to(self.target.parent, target_is_directory=True)
        for target in (link, parent / self.target.name):
            with self.subTest(target=target), self.assertRaises(ValueError):
                d.install(target, platform="crosspoint", backup_root=self.backups)
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.backups.exists())

    def test_symlinked_destination_rejected(self):
        d.fetch(self.downloads)
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "dictionaries").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.backups.exists())

    def test_shadow_files_and_hidden_dictionary_rejected_without_writes(self):
        d.fetch(self.downloads)
        self.directory.mkdir(parents=True)
        hidden = self.target / ".dictionaries/test"
        for shadow in (self.directory / "test.dict", self.directory / "another.idx", hidden):
            with self.subTest(shadow=shadow):
                if shadow == hidden:
                    shadow.mkdir(parents=True)
                else:
                    shadow.write_bytes(b"keep")
                for check in (True, False):
                    with self.assertRaises(ValueError):
                        self.install(check=check)
                self.assertFalse((self.directory / "test.ifo").exists())
                self.assertFalse(self.backups.exists())
                if shadow == hidden:
                    shadow.rmdir()
                else:
                    self.assertEqual(shadow.read_bytes(), b"keep")
                    shadow.unlink()

    def test_corrupt_cache_does_not_write_device(self):
        d.fetch(self.downloads)
        d.archive_path(self.pin).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.install()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.backups.exists())

    def test_64_bit_offsets_rejected_before_writes(self):
        self.files["test.ifo"] += b"idxoffsetbits=64\n"
        self.make_archive()
        d.fetch(self.downloads)
        for check in (True, False):
            with self.subTest(check=check), self.assertRaisesRegex(ValueError, "32-bit"):
                self.install(check=check)
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.backups.exists())

    def test_backups_must_stay_outside_device(self):
        d.fetch(self.downloads)
        backup_root = self.target / "backups"
        with self.assertRaisesRegex(ValueError, "outside"):
            d.install(self.target, platform="crosspoint", backup_root=backup_root)
        self.assertFalse(backup_root.exists())
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import fonts


def ttf(family, style, variable=False):
    """Minimal table fixture for installer validation, not a renderable font."""
    name = family.encode("utf-16-be")
    head = bytearray(46)
    struct.pack_into(">H", head, 44, (1 if "Bold" in style else 0) | (2 if "Italic" in style else 0))
    tables = {b"head": bytes(head), b"name": struct.pack(">9H", 0, 1, 18, 3, 1, 0x409, 1, len(name), 0) + name,
              b"glyf": b"", b"loca": b"", b"cmap": b""}
    if variable:
        tables[b"fvar"] = b""
    offset = 12 + 16 * len(tables)
    records, payload = b"", b""
    for tag, data in tables.items():
        records += struct.pack(">4sIII", tag, 0, offset, len(data))
        payload += data
        offset += len(data)
    return b"\x00\x01\x00\x00" + struct.pack(">4H", len(tables), 0, 0, 0) + records + payload


class FontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cache = self.root / "cache"
        self.target = self.root / "reader"
        (self.target / "plugins").mkdir(parents=True)
        self.backups = self.root / "backups"
        patcher = patch.object(fonts, "CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        for family in fonts.profile()["families"]:
            files = {f"{family}-{s}.ttf": ttf(family, s) for s in fonts.profile()["styles"]}
            if family == "Literata":
                files["OFL.txt"] = b"license"
            fonts.save_family(family, files, fonts.profile()["literata"] if family == "Literata" else "private")

    def install(self, **kwargs):
        return fonts.install(self.target, backup_root=self.backups, **kwargs)

    def test_both_layouts_identical_and_repeat_is_noop(self):
        for device, directory in (("koreader", "fonts"), ("x4pro", ".fonts")):
            self.assertFalse(self.install(device=device, check=True))
            self.assertFalse((self.target / directory).exists())
            self.assertTrue(self.install(device=device))
            backups = list(self.backups.iterdir())
            self.assertTrue(self.install(device=device))
            self.assertEqual(backups, list(self.backups.iterdir()))
            self.assertTrue(self.install(device=device, check=True))
            for relative, source in fonts.prepared_files():
                self.assertEqual(source.read_bytes(), (self.target / directory / relative).read_bytes())

    def test_missing_family_fails_before_writes(self):
        (self.cache / "Bookerly/Bookerly-Bold.ttf").unlink()
        with self.assertRaisesRegex(ValueError, "corrupted"):
            self.install()
        self.assertFalse((self.target / "fonts").exists())
        self.assertFalse(self.backups.exists())

    def test_explicit_partial_install(self):
        (self.cache / "Bookerly/manifest.json").unlink()
        self.assertTrue(self.install(families=["Literata"]))
        self.assertFalse((self.target / "fonts/Bookerly").exists())

    def test_corruption_rejected(self):
        (self.cache / "Literata/Literata-Regular.ttf").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "corrupted"):
            self.install()
        self.assertFalse(self.backups.exists())

    def test_backup_and_unmanaged_preservation(self):
        directory = self.target / "fonts/Bookerly"
        directory.mkdir(parents=True)
        (directory / "Bookerly-Regular.ttf").write_bytes(b"previous")
        (directory / "personal.txt").write_text("keep")
        self.install()
        backup = next(self.backups.iterdir())
        self.assertEqual((backup / "fonts/Bookerly/Bookerly-Regular.ttf").read_bytes(), b"previous")
        self.assertEqual((directory / "personal.txt").read_text(), "keep")
        manifest = json.loads((backup / "fonts.json").read_text())
        self.assertFalse(manifest["files"]["fonts/Literata/Literata-Regular.ttf"]["existed"])

    def test_symlink_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "fonts").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_cpfont_shadow_refused(self):
        directory = self.target / ".fonts/Literata"
        directory.mkdir(parents=True)
        (directory / "Literata_14.cpfont").write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.install(device="x4pro")
        self.assertFalse((self.target / ".fonts/Bookerly").exists())

    def test_invalid_target(self):
        with self.assertRaises(ValueError):
            fonts.install(self.root / "missing")

    def test_loose_crosspoint_family_conflict(self):
        (self.target / ".fonts").mkdir()
        (self.target / ".fonts/Bookerly.ttf").write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "conflicting loose"):
            self.install(device="x4pro")
        self.assertFalse(self.backups.exists())

    def test_directory_in_place_of_file(self):
        (self.target / "fonts/Literata/Literata-Regular.ttf").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.install()
        self.assertFalse((self.target / "fonts/Bookerly").exists())

    def test_validate_rejects_wrong_family_style_variable_and_truncated(self):
        for data in (ttf("Other", "Regular"), ttf("Bookerly", "Bold"),
                     ttf("Bookerly", "Regular", variable=True), b"\x00\x01\x00\x00", b"html"):
            with self.subTest(data=data[:16]), self.assertRaises(ValueError):
                fonts.validate_ttf(data, "Bookerly", "Regular")

    def test_import_bookerly_is_complete_and_idempotent(self):
        source = self.cache / "Bookerly"
        with patch.object(fonts, "CACHE", self.root / "new-cache"):
            fonts.import_bookerly(source)
            fonts.import_bookerly(source)
            self.assertEqual(len(fonts.prepared_files(["Bookerly"])), 4)

    def test_fetch_verified_and_cached_offline(self):
        archive = self.root / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for style in fonts.profile()["styles"]:
                bundle.writestr(f"fonts/ttf/Literata-{style}.ttf", ttf("Literata", style))
            bundle.writestr("OFL.txt", b"license")
        config = fonts.profile()
        config["literata"]["sha256"] = fonts.sha(archive.read_bytes())

        def download(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(archive.read_bytes())

        with patch.object(fonts, "CACHE", self.root / "fresh"), patch.object(fonts, "profile", return_value=config), \
                patch.object(fonts.subprocess, "run", side_effect=download) as network:
            fonts.fetch_literata()
            fonts.fetch_literata()
            network.assert_called_once()
            self.assertEqual(len(fonts.prepared_files(["Literata"])), 5)

    def test_download_checksum_failure_no_prepared_cache(self):
        def download(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"not the release")

        with patch.object(fonts, "CACHE", self.root / "fresh"), \
                patch.object(fonts.subprocess, "run", side_effect=download):
            with self.assertRaisesRegex(ValueError, "checksum"):
                fonts.fetch_literata()
            self.assertFalse((fonts.CACHE / "Literata").exists())


if __name__ == "__main__":
    unittest.main()

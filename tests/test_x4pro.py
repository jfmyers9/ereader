import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("x4pro", ROOT / "scripts/x4pro.py")
x4pro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x4pro)


class X4ProTest(unittest.TestCase):
    def test_correct_environment_and_explicit_port(self):
        build = x4pro.pio_command("build")
        self.assertEqual(build[-2:], ["--environment", "x4pro"])
        self.assertNotIn("upload", build)
        flash = x4pro.pio_command("flash", "/dev/cu.usbmodem123")
        self.assertEqual(flash[-4:], ["--target", "upload", "--upload-port", "/dev/cu.usbmodem123"])
        self.assertNotIn("erase", flash)
        for port in (None, "", " ", "--bad"):
            with self.assertRaises(ValueError):
                x4pro.pio_command("flash", port)

    def test_flash_requires_confirmation_before_any_action(self):
        with patch.object(x4pro, "run") as run, patch.object(x4pro, "validate_source") as validate:
            for args in (["flash"], ["flash", "--port", "test"]):
                with self.assertRaises(SystemExit) as error:
                    x4pro.main(args)
                self.assertEqual(error.exception.code, 2)
            run.assert_not_called()
            validate.assert_not_called()

    def test_dry_run_does_not_execute_or_create_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            toolchain = Path(directory) / "toolchain"
            with patch.object(x4pro, "TOOLCHAIN", toolchain), \
                    patch.object(x4pro, "validate_source"), \
                    patch.object(x4pro.subprocess, "run") as run, \
                    patch.object(x4pro.dictionaries, "fetch") as fetch, \
                    patch.object(x4pro.fonts, "fetch_family") as fetch_fonts:
                for args in (["prepare", "--dry-run"], ["build", "--dry-run"],
                             ["ports", "--dry-run"], ["flash", "--port", "test", "--dry-run"]):
                    x4pro.main(args)
                run.assert_not_called()
                fetch.assert_not_called()
                fetch_fonts.assert_not_called()
                self.assertFalse(toolchain.exists())

    def test_confirmed_flash_dispatches_only_requested_port(self):
        with patch.object(x4pro, "validate_source"), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(x4pro.subprocess, "run") as run:
            x4pro.main(["flash", "--port", "/dev/cu.reader", "--confirm-flash"])
            command = run.call_args.args[0]
            self.assertEqual(command[-4:], ["--target", "upload", "--upload-port", "/dev/cu.reader"])
            self.assertIn("x4pro", command)
            self.assertEqual(run.call_count, 1)

    def test_prepare_reuses_existing_environment(self):
        with patch.object(x4pro, "validate_source"), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(x4pro.venv, "EnvBuilder") as builder, \
                patch.object(x4pro, "run") as run, \
                patch.object(x4pro.dictionaries, "fetch") as fetch, \
                patch.object(x4pro.fonts, "fetch_family") as fetch_fonts:
            x4pro.prepare()
            builder.assert_not_called()
            fetch.assert_called_once_with()
            self.assertEqual([call.args[0] for call in fetch_fonts.call_args_list],
                             ["Bookerly", "Literata"])
            self.assertNotIn("--upgrade", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-1], x4pro.REQUIREMENTS)

    def cli_code(self, args):
        try:
            return x4pro.main(args) or 0
        except SystemExit as error:
            return error.code

    def test_dictionaries_routes_without_toolchain_or_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(x4pro, "validate_source", side_effect=AssertionError("No source needed")), \
                    patch.object(x4pro, "executable", side_effect=AssertionError("No toolchain needed")), \
                    patch.object(x4pro, "run", side_effect=AssertionError("No build or flash")), \
                    patch.object(x4pro.dictionaries, "fetch", side_effect=AssertionError("No download")), \
                    patch.object(x4pro.dictionaries, "install", return_value=None) as install:
                for flag in ([], ["--check"], ["--dry-run"], ["--check", "--dry-run"]):
                    with self.subTest(flag=flag):
                        self.assertEqual(self.cli_code(["dictionaries", "--sd-root", str(target)] + flag), 0)
                        args, kwargs = install.call_args
                        self.assertEqual(Path(args[0]), target)
                        self.assertEqual(kwargs["platform"], "crosspoint")
                        self.assertEqual(kwargs.get("check", False), bool(flag))
                self.assertEqual(install.call_count, 4)

    def test_setup_checks_both_before_apply_without_build_or_download(self):
        events = []

        def dictionary_install(target, **kwargs):
            self.assertEqual(target, Path("/unused"))
            self.assertEqual(kwargs["platform"], "crosspoint")
            events.append(("dictionaries", kwargs.get("check", False)))
            return False if kwargs.get("check") else None

        def font_install(target, **kwargs):
            self.assertEqual(target, Path("/unused"))
            self.assertEqual(kwargs["device"], "x4pro")
            events.append(("fonts", kwargs.get("check", False)))
            return False if kwargs.get("check") else True

        with patch.object(x4pro.dictionaries, "install", side_effect=dictionary_install), \
                patch.object(x4pro.fonts, "install", side_effect=font_install), \
                patch.object(x4pro, "validate_source", side_effect=AssertionError("No source needed")), \
                patch.object(x4pro, "executable", side_effect=AssertionError("No toolchain needed")), \
                patch.object(x4pro, "run", side_effect=AssertionError("No build or flash")), \
                patch.object(x4pro.dictionaries, "fetch", side_effect=AssertionError("No download")), \
                patch.object(x4pro.fonts, "fetch_family", side_effect=AssertionError("No download")):
            self.assertEqual(self.cli_code(["setup", "--sd-root", "/unused"]), 0)
        self.assertEqual(events, [("dictionaries", True), ("fonts", True),
                                  ("dictionaries", False), ("fonts", False)])

    def test_setup_check_reports_either_drift_and_never_applies(self):
        for flags in (["--check"], ["--dry-run"], ["--check", "--dry-run"]):
            for dictionaries_current in (False, True):
                for fonts_current in (False, True):
                    with self.subTest(flags=flags, dictionaries=dictionaries_current, fonts=fonts_current), \
                            patch.object(x4pro.dictionaries, "install",
                                         return_value=None if dictionaries_current else False) as dictionaries, \
                            patch.object(x4pro.fonts, "install", return_value=fonts_current) as fonts:
                        self.assertEqual(self.cli_code(["setup", "--sd-root", "/unused", *flags]),
                                         0 if dictionaries_current and fonts_current else 1)
                        dictionaries.assert_called_once_with(Path("/unused"), check=True, platform="crosspoint")
                        fonts.assert_called_once_with(Path("/unused"), device="x4pro", check=True)

    def test_setup_preflight_failure_prevents_all_writes(self):
        for component in (x4pro.dictionaries, x4pro.fonts):
            for error in (ValueError("conflicting files"), OSError("missing cache"),
                          zipfile.BadZipFile("invalid archive")):
                with self.subTest(component=component, error=error), \
                        patch.object(x4pro.dictionaries, "install", return_value=False) as dictionaries, \
                        patch.object(x4pro.fonts, "install", return_value=False) as fonts:
                    failing = dictionaries if component is x4pro.dictionaries else fonts
                    failing.side_effect = error
                    self.assertEqual(self.cli_code(["setup", "--sd-root", "/unused"]), 2)
                    for installer in (dictionaries, fonts):
                        self.assertTrue(all(call.kwargs.get("check") for call in installer.call_args_list))

    def test_setup_apply_failure_is_reported_without_running_later_installer(self):
        with patch.object(x4pro.dictionaries, "install", side_effect=[False, OSError("card removed")]), \
                patch.object(x4pro.fonts, "install", return_value=False) as fonts:
            self.assertEqual(self.cli_code(["setup", "--sd-root", "/unused"]), 2)
            fonts.assert_called_once_with(Path("/unused"), device="x4pro", check=True)

    def test_setup_installer_integration_is_read_only_then_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "sd"
            (target / ".crosspoint").mkdir(parents=True)
            state = target / ".crosspoint/settings.json"
            state.write_bytes(b"private reading preferences")
            source = root / "font-cache"
            source.mkdir()
            font_files = []
            for family in ("Bookerly", "Literata"):
                for style in ("Regular", "Bold", "Italic", "BoldItalic"):
                    relative = Path(family) / f"{family}-{style}.ttf"
                    cached = source / relative.name
                    cached.write_bytes(relative.name.encode())
                    font_files.append((relative, cached))
            pin = {"id": "test", "members": ["test.ifo", "test.idx", "test.dict.dz"]}

            def extract(pin, destination):
                destination.mkdir(parents=True)
                for name in pin["members"]:
                    (destination / name).write_bytes(b"fixture data")

            # Asset parsing has dedicated suites. Exercise both real installers,
            # their destinations/backups, and the CLI using prepared fixtures.
            with patch.object(x4pro.fonts, "prepared_files", return_value=font_files), \
                    patch.object(x4pro.fonts, "ROOT", root), \
                    patch.object(x4pro.dictionaries, "ROOT", root), \
                    patch.object(x4pro.dictionaries, "pins", return_value=[pin]), \
                    patch.object(x4pro.dictionaries, "extract", side_effect=extract):
                args = ["setup", "--sd-root", str(target)]
                backups = root / ".local/backups"
                for flag in ("--check", "--dry-run"):
                    self.assertEqual(self.cli_code([*args, flag]), 1)
                    self.assertEqual(list(target.iterdir()), [target / ".crosspoint"])
                    self.assertFalse(backups.exists())
                # A font conflict must prevent dictionary writes, too.
                legacy = target / ".fonts/Bookerly/Bookerly_14.cpfont"
                legacy.parent.mkdir(parents=True)
                legacy.write_bytes(b"legacy")
                self.assertEqual(self.cli_code(args), 2)
                self.assertEqual(legacy.read_bytes(), b"legacy")
                self.assertFalse((target / "dictionaries").exists())
                self.assertFalse(backups.exists())
                legacy.unlink()
                old_font = target / ".fonts/Bookerly/Bookerly-Regular.ttf"
                old_font.write_bytes(b"previous font")
                self.assertEqual(self.cli_code(args), 0)
                for relative, cached in font_files:
                    self.assertEqual((target / ".fonts" / relative).read_bytes(), cached.read_bytes())
                for name in pin["members"]:
                    self.assertTrue((target / "dictionaries/test" / name).is_file())
                saved_font = list(backups.glob("*/.fonts/Bookerly/Bookerly-Regular.ttf"))
                self.assertEqual(len(saved_font), 1)
                self.assertEqual(saved_font[0].read_bytes(), b"previous font")
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                          for p in root.rglob("*") if p.is_file()}
                self.assertEqual(self.cli_code([*args, "--check"]), 0)
                self.assertEqual(self.cli_code(args), 0)
                after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                         for p in root.rglob("*") if p.is_file()}
                self.assertEqual(before, after)
                self.assertEqual(state.read_bytes(), b"private reading preferences")

    def test_setup_rejects_missing_or_symlinked_marker_before_fonts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside = root / "outside"
            outside.mkdir()
            marker = root / ".crosspoint"
            with patch.object(x4pro.fonts, "install") as fonts:
                for symlink in (False, True):
                    if symlink:
                        marker.symlink_to(outside, target_is_directory=True)
                    self.assertEqual(self.cli_code(["setup", "--sd-root", str(root)]), 2)
                fonts.assert_not_called()
            self.assertFalse((root / ".local").exists())

    def test_dictionaries_exit_codes(self):
        for flag in ("--check", "--dry-run"):
            with self.subTest(flag=flag), \
                    patch.object(x4pro.dictionaries, "install", return_value=False):
                self.assertEqual(self.cli_code(["dictionaries", "--sd-root", "/unused", flag]), 1)
        for error in (ValueError("invalid root"), OSError("unavailable SD")):
            with self.subTest(error=error), \
                    patch.object(x4pro.dictionaries, "install", side_effect=error):
                self.assertEqual(self.cli_code(["dictionaries", "--sd-root", "/unused"]), 2)

    def test_dictionaries_rejects_invalid_options_before_work(self):
        invalid = [
            ["setup"],
            ["setup", "--sd-root", "/unused", "--port", "test"],
            ["setup", "--sd-root", "/unused", "--confirm-flash"],
            ["dictionaries"],
            ["dictionaries", "--sd-root", "/unused", "--port", "test"],
            ["dictionaries", "--sd-root", "/unused", "--confirm-flash"],
            ["prepare", "--sd-root", "/unused"],
            ["build", "--check"],
            ["ports", "--sd-root", "/unused"],
            ["flash", "--port", "test", "--confirm-flash", "--check"],
        ]
        with patch.object(x4pro.dictionaries, "install") as install, \
                patch.object(x4pro.fonts, "install") as font_install, \
                patch.object(x4pro.dictionaries, "fetch") as fetch, \
                patch.object(x4pro, "validate_source") as validate, \
                patch.object(x4pro, "run") as run:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertEqual(self.cli_code(args), 2)
            install.assert_not_called()
            font_install.assert_not_called()
            fetch.assert_not_called()
            validate.assert_not_called()
            run.assert_not_called()

    def test_prepare_fetches_only_after_successful_toolchain_install(self):
        with patch.object(x4pro, "validate_source"), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(x4pro, "run", side_effect=OSError("toolchain failed")), \
                patch.object(x4pro.dictionaries, "fetch") as fetch, \
                patch.object(x4pro.fonts, "fetch_family") as fetch_fonts:
            with self.assertRaises(OSError):
                x4pro.prepare()
            fetch.assert_not_called()
            fetch_fonts.assert_not_called()

    def test_source_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "platformio.ini").touch()
            with patch.object(x4pro, "SOURCE", source):
                for status in ("-abc sdk", "+abc sdk", "Uabc sdk"):
                    with patch.object(x4pro.subprocess, "check_output", return_value=status):
                        with self.assertRaisesRegex(ValueError, "pins"):
                            x4pro.validate_source()
                with patch.object(x4pro.subprocess, "check_output", side_effect=[" abc sdk", " M file"]):
                    with self.assertRaisesRegex(ValueError, "Commit"):
                        x4pro.validate_source()
                with patch.object(x4pro.subprocess, "check_output", side_effect=[" abc sdk", "", "revision"]):
                    self.assertEqual(x4pro.validate_source(), "revision")
                (source / "platformio.local.ini").touch()
                with patch.object(x4pro.subprocess, "check_output", side_effect=[" abc sdk", ""]):
                    with self.assertRaisesRegex(ValueError, "overrides"):
                        x4pro.validate_source()


if __name__ == "__main__":
    unittest.main()

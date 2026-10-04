import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                    patch.object(x4pro.dictionaries, "fetch") as fetch:
                for args in (["prepare", "--dry-run"], ["build", "--dry-run"],
                             ["ports", "--dry-run"], ["flash", "--port", "test", "--dry-run"]):
                    x4pro.main(args)
                run.assert_not_called()
                fetch.assert_not_called()
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
                patch.object(x4pro.dictionaries, "fetch") as fetch:
            x4pro.prepare()
            builder.assert_not_called()
            fetch.assert_called_once_with()
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
            ["dictionaries"],
            ["dictionaries", "--sd-root", "/unused", "--port", "test"],
            ["dictionaries", "--sd-root", "/unused", "--confirm-flash"],
            ["prepare", "--sd-root", "/unused"],
            ["build", "--check"],
            ["ports", "--sd-root", "/unused"],
            ["flash", "--port", "test", "--confirm-flash", "--check"],
        ]
        with patch.object(x4pro.dictionaries, "install") as install, \
                patch.object(x4pro.dictionaries, "fetch") as fetch, \
                patch.object(x4pro, "validate_source") as validate, \
                patch.object(x4pro, "run") as run:
            for args in invalid:
                with self.subTest(args=args):
                    self.assertEqual(self.cli_code(args), 2)
            install.assert_not_called()
            fetch.assert_not_called()
            validate.assert_not_called()
            run.assert_not_called()

    def test_prepare_fetches_only_after_successful_toolchain_install(self):
        with patch.object(x4pro, "validate_source"), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(x4pro, "run", side_effect=OSError("toolchain failed")), \
                patch.object(x4pro.dictionaries, "fetch") as fetch:
            with self.assertRaises(OSError):
                x4pro.prepare()
            fetch.assert_not_called()

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

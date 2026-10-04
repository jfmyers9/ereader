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
                    patch.object(x4pro.subprocess, "run") as run:
                for args in (["prepare", "--dry-run"], ["build", "--dry-run"],
                             ["ports", "--dry-run"], ["flash", "--port", "test", "--dry-run"]):
                    x4pro.main(args)
                run.assert_not_called()
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
                patch.object(x4pro, "run") as run:
            x4pro.prepare()
            builder.assert_not_called()
            self.assertNotIn("--upgrade", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-1], x4pro.REQUIREMENTS)

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

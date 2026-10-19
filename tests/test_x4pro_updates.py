import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import x4pro


class FirmwareUpdatesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.project / "crosspoint-reader"
        self.source.mkdir()
        self.git(self.source, "init", "-q", "-b", "personal/develop")
        (self.source / "platformio.ini").write_text("[env:x4pro]\n")
        self.commit(self.source, "initial firmware")
        self.base = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "branch", "develop")
        self.remotes = {}
        for remote, branch in x4pro.UPDATE_BRANCHES:
            destination = self.root / remote
            self.git(self.root, "clone", "-q", str(self.source), str(destination))
            self.git(destination, "checkout", "-q", "-B", branch, f"origin/{branch}")
            self.git(self.source, "remote", "add", remote, str(destination))
            self.remotes[remote] = destination
        self.git(self.project, "init", "-q", "-b", "main")
        self.git(self.project, "update-index", "--add", "--cacheinfo",
                 f"160000,{self.base},crosspoint-reader")
        self.git(self.project, "-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "commit", "-qm", "pin firmware")
        for name, value in (("ROOT", self.project), ("SOURCE", self.source)):
            patcher = patch.object(x4pro, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def git(self, directory, *args):
        return subprocess.check_output(["git", "-C", str(directory), *args],
                                       text=True, stderr=subprocess.PIPE).strip()

    def commit(self, directory, message):
        self.git(directory, "add", ".")
        self.git(directory, "-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "commit", "-qm", message)

    def advance(self, remote):
        repo = self.remotes[remote]
        (repo / "change.txt").write_text("new firmware change\n")
        self.commit(repo, f"new {remote} change")
        return self.git(repo, "rev-parse", "HEAD")

    def cli_code(self, args):
        try:
            return x4pro.main(args) or 0
        except SystemExit as error:
            return error.code

    def test_fetch_report_preserves_checkout_branch_pin_and_index(self):
        tip = self.advance("upstream")
        self.advance("origin")
        (self.source / "personal.txt").write_text("personal feature\n")
        self.commit(self.source, "personal change")
        before = [self.git(self.source, "rev-parse", "HEAD"),
                  self.git(self.source, "branch", "--show-current"),
                  self.git(self.project, "ls-tree", "HEAD"),
                  self.git(self.project, "ls-files", "--stage")]
        with patch.object(x4pro, "verify_firmware") as verify:
            self.assertEqual(self.cli_code(["updates"]), 1)
            verify.assert_not_called()
        self.assertEqual(self.git(self.source, "rev-parse", "upstream/develop"), tip)
        after = [self.git(self.source, "rev-parse", "HEAD"),
                 self.git(self.source, "branch", "--show-current"),
                 self.git(self.project, "ls-tree", "HEAD"),
                 self.git(self.project, "ls-files", "--stage")]
        self.assertEqual(before, after)
        self.assertEqual(self.git(self.source, "status", "--porcelain"), "")
        output = self.output.getvalue()
        self.assertIn("Checkout-only commits: 1; incoming commits: 1", output)
        self.assertIn("Histories diverge", output)
        self.assertIn("Checkout differs from the committed pin", output)
        self.assertIn("new upstream change", output)
        self.assertIn("change.txt", output)
        self.assertIn("Tests/build NOT RUN", output)
        self.assertIn("BookOrbit", output)

    def test_cached_refs_are_explicit_and_not_refreshed(self):
        self.assertEqual(self.cli_code(["updates"]), 0)
        self.advance("upstream")
        with patch.object(x4pro, "run", side_effect=AssertionError("No fetch")):
            self.assertEqual(self.cli_code(["updates", "--no-fetch"]), 0)
        self.assertIn("not a check of live remote state", self.output.getvalue())
        self.assertEqual(self.cli_code(["updates"]), 1)

    def test_personal_commits_alone_are_not_incoming_updates(self):
        (self.source / "personal.txt").write_text("personal\n")
        self.commit(self.source, "personal")
        head = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.project, "update-index", "--cacheinfo", f"160000,{head},crosspoint-reader")
        self.git(self.project, "-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "commit", "-qm", "update pin")
        self.assertEqual(self.cli_code(["updates"]), 0)
        self.assertIn("Checkout-only commits: 1; incoming commits: 0", self.output.getvalue())

    def test_verify_receives_checkout_not_remote_tip(self):
        self.advance("upstream")
        with patch.object(x4pro, "verify_firmware") as verify:
            self.assertEqual(self.cli_code(["updates", "--verify"]), 1)
            verify.assert_called_once_with(self.base, False)

    def test_rewritten_fork_only_updates_remote_tracking_ref(self):
        old_tip = self.advance("origin")
        self.assertEqual(self.cli_code(["updates"]), 1)
        fork = self.remotes["origin"]
        self.git(fork, "checkout", "-q", "-B", "personal/develop", self.base)
        (fork / "replacement.txt").write_text("rebased personal feature\n")
        self.commit(fork, "replacement personal commit")
        new_tip = self.git(fork, "rev-parse", "HEAD")
        self.assertNotEqual(old_tip, new_tip)
        self.assertEqual(self.cli_code(["updates"]), 1)
        self.assertEqual(self.git(self.source, "rev-parse", "origin/personal/develop"), new_tip)
        self.assertEqual(self.git(self.source, "rev-parse", "personal/develop"), self.base)
        self.assertEqual(self.git(self.project, "ls-tree", "HEAD", "crosspoint-reader").split()[2], self.base)

    def test_verification_failure_takes_precedence_over_updates_exit_code(self):
        self.advance("upstream")
        with patch.object(x4pro, "verify_firmware", side_effect=ValueError("tests failed")):
            self.assertEqual(self.cli_code(["updates", "--verify"]), 2)

    def test_dry_run_never_fetches_or_runs_verification(self):
        with patch.object(x4pro, "verification_commands", return_value=[("tests", ["test-command"])]), \
                patch.object(x4pro, "run", wraps=x4pro.run) as run:
            self.assertEqual(self.cli_code(["updates", "--verify", "--dry-run"]), 0)
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertTrue(call.kwargs["dry_run"] if "dry_run" in call.kwargs else call.args[1])
        self.assertIn("test-command", self.output.getvalue())
        self.assertEqual(self.git(self.source, "for-each-ref", "refs/remotes"), "")

    def test_errors_never_become_no_updates_or_start_verification(self):
        with patch.object(x4pro, "verify_firmware") as verify:
            self.assertEqual(self.cli_code(["updates", "--no-fetch", "--verify"]), 2)
            (self.source / "platformio.ini").write_text("dirty\n")
            self.assertEqual(self.cli_code(["updates", "--verify"]), 2)
            self.git(self.source, "restore", "platformio.ini")
            self.git(self.source, "remote", "remove", "upstream")
            self.assertEqual(self.cli_code(["updates", "--verify"]), 2)
            verify.assert_not_called()

    def test_fetch_failure_does_not_use_stale_ref(self):
        self.assertEqual(self.cli_code(["updates"]), 0)
        self.git(self.source, "remote", "set-url", "upstream", str(self.root / "missing"))
        with patch.object(x4pro, "verify_firmware") as verify:
            self.assertEqual(self.cli_code(["updates", "--verify"]), 2)
            verify.assert_not_called()

    def test_unrelated_history_is_an_error(self):
        repo = self.remotes["upstream"]
        self.git(repo, "checkout", "-q", "--orphan", "unrelated")
        self.commit(repo, "unrelated root")
        self.git(repo, "branch", "-f", "develop", "HEAD")
        self.assertEqual(self.cli_code(["updates"]), 2)


class FirmwareVerificationTest(unittest.TestCase):
    def test_verification_commands_only_test_and_build(self):
        commands = dict(x4pro.verification_commands(dry_run=True))
        self.assertEqual(list(commands), ["repo-tests", "host-configure", "host-build",
                                          "host-tests", "x4pro-build"])
        self.assertIn("--no-tests=error", commands["host-tests"])
        self.assertEqual(commands["x4pro-build"][-2:], ["--environment", "x4pro"])
        self.assertNotIn("upload", commands["x4pro-build"])
        self.assertIn("test", commands["host-configure"][2])

    def test_missing_tools_fail_before_execution(self):
        with patch.object(Path, "is_file", return_value=False), \
                patch.object(x4pro.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Missing verification tool"):
                x4pro.verification_commands()

    def test_failure_records_log_and_stops_remaining_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "check.log"
            descriptor = os.open(log, os.O_CREAT | os.O_WRONLY, 0o600)
            with patch.object(x4pro, "verification_commands", return_value=[
                    ("host-tests", ["tests"]), ("x4pro-build", ["build"])]), \
                    patch.object(x4pro.tempfile, "mkstemp", return_value=(descriptor, str(log))), \
                    patch.object(x4pro.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
                with self.assertRaisesRegex(ValueError, "host-tests failed.*check.log"):
                    x4pro.verify_firmware("abc123")
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)

    def test_changed_checkout_invalidates_results(self):
        with patch.object(x4pro, "verification_commands", return_value=[]), \
                patch.object(x4pro, "validate_source", return_value="different"):
            with self.assertRaisesRegex(ValueError, "changed during verification"):
                x4pro.verify_firmware("original")

    def test_invalid_cli_options_fail_before_work(self):
        invalid = [["updates", "--port", "test"], ["updates", "--confirm-flash"],
                   ["updates", "--sd-root", "/unused"], ["updates", "--check"],
                   ["build", "--verify"], ["prepare", "--no-fetch"]]
        with patch.object(x4pro, "updates") as updates, patch.object(x4pro, "run") as run:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    x4pro.main(args)
                self.assertEqual(error.exception.code, 2)
            updates.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

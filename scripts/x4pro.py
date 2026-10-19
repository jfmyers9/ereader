#!/usr/bin/env python3
"""Prepare X4 Pro firmware, fonts, and dictionaries; set up the SD card or explicitly flash."""

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dictionaries, fonts

SOURCE = ROOT / "crosspoint-reader"
TOOLCHAIN = ROOT / ".local/crosspoint-toolchain"
REQUIREMENTS = ROOT / "profiles/x4pro/requirements.txt"
UPDATE_BRANCHES = (("origin", "personal/develop"), ("upstream", "develop"))


def executable(name):
    return TOOLCHAIN / ("Scripts" if os.name == "nt" else "bin") / (
        name + ".exe" if os.name == "nt" else name
    )


def run(command, dry_run=False):
    command = [str(part) for part in command]
    print("+ " + shlex.join(command), flush=True)
    if not dry_run:
        subprocess.run(command, cwd=ROOT, check=True)


def validate_source():
    if not (SOURCE / "platformio.ini").is_file():
        raise ValueError("Missing CrossPoint checkout; run git submodule update --init --recursive")
    nested = subprocess.check_output(
        ["git", "-C", str(SOURCE), "submodule", "status", "--recursive"], text=True
    )
    if any(line and line[0] in "-+U" for line in nested.splitlines()):
        raise ValueError("Nested submodules must match their pins; initialize them explicitly")
    dirty = subprocess.check_output(
        ["git", "-C", str(SOURCE), "status", "--porcelain"], text=True
    ).strip()
    if dirty or (SOURCE / "platformio.local.ini").exists():
        raise ValueError("Commit CrossPoint changes and remove local PlatformIO overrides before setup")
    revision = subprocess.check_output(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True
    ).strip()
    print(f"CrossPoint revision: {revision}; environment: x4pro", flush=True)
    return revision


def prepare(dry_run=False):
    # Do not update branches or reset submodules implicitly: the gitlinks are the pins.
    validate_source()
    if not executable("python").exists():
        print(f"Create isolated toolchain: {TOOLCHAIN}", flush=True)
        if not dry_run:
            venv.EnvBuilder(with_pip=True).create(TOOLCHAIN)
    # No --upgrade: repeat setup leaves already-satisfied dependencies alone.
    run([executable("python"), "-m", "pip", "install", "-r", REQUIREMENTS], dry_run)
    if dry_run:
        print("Would fetch and verify pinned GCIDE and French Wiktionnaire dictionaries")
        print("Would fetch and verify pinned Bookerly and Literata TTF fonts")
    else:
        dictionaries.fetch()
        for family in fonts.profile()["families"]:
            fonts.fetch_family(family)


def setup(sd_root, check=False):
    # Dictionary preflight also validates the CrossPoint SD marker. Check both
    # sets before any writes; their backups remain separate, not one transaction.
    dictionary_result = dictionaries.install(sd_root, check=True, platform="crosspoint")
    font_result = fonts.install(sd_root, device="x4pro", check=True)
    if check:
        return dictionary_result is not False and font_result is not False
    dictionaries.install(sd_root, platform="crosspoint")
    fonts.install(sd_root, device="x4pro")
    print("SD setup complete. Safely eject, leave USB Drive mode, and restart the reader. "
          "Select Font Family and Dictionary under Settings > Reader. Firmware was not flashed.")
    return True


def pio_command(action, port=None):
    pio = executable("pio")
    if action == "ports":
        return [pio, "device", "list"]
    command = [pio, "run", "--project-dir", SOURCE, "--environment", "x4pro"]
    if action == "flash":
        if not port or not port.strip() or port.startswith("-"):
            raise ValueError("Flash requires an explicit --port; automatic selection is disabled")
        command += ["--target", "upload", "--upload-port", port]
    return command


def git_output(directory, *args):
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True
    ).strip()


def verification_commands(dry_run=False):
    tools = {}
    for name in ("cmake", "ctest", "pio"):
        local = executable(name)
        path = str(local) if local.is_file() else shutil.which(name)
        if not path and not dry_run:
            raise ValueError(f"Missing verification tool: {name}; prepare the toolchain "
                             "and install CMake/CTest before using --verify")
        tools[name] = path or str(local)
    build = SOURCE / "build/test"
    return [
        ("repo-tests", [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests")]),
        ("host-configure", [tools["cmake"], "-S", str(SOURCE / "test"), "-B", str(build),
                            "-DCMAKE_BUILD_TYPE=Release", "-DFETCHCONTENT_UPDATES_DISCONNECTED=ON"]),
        ("host-build", [tools["cmake"], "--build", str(build), "--parallel", "4"]),
        ("host-tests", [tools["ctest"], "--test-dir", str(build), "--output-on-failure", "--no-tests=error"]),
        ("x4pro-build", [tools["pio"], "run", "--project-dir", str(SOURCE), "--environment", "x4pro"]),
    ]


def verify_firmware(revision, dry_run=False):
    print(f"Verification target: current checkout {revision}, NOT unintegrated remote commits.", flush=True)
    env = os.environ.copy()
    env["PATH"] = str(executable("python").parent) + os.pathsep + env.get("PATH", "")
    for label, command in verification_commands(dry_run):
        if dry_run:
            run(command, dry_run=True)
            continue
        descriptor, log = tempfile.mkstemp(prefix=f"x4pro-{revision[:12]}-{label}-", suffix=".log")
        print(f"Running {label}; log: {log}", flush=True)
        with os.fdopen(descriptor, "w") as output:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f"{label} failed (exit {result.returncode}); see {log}. "
                             "Remaining verification steps were not run.")
        print(f"PASS: {label}", flush=True)
    if not dry_run and validate_source() != revision:
        raise ValueError("Checkout changed during verification; results do not cover the current revision")


def updates(no_fetch=False, verify=False, dry_run=False):
    revision = validate_source()
    pin_entry = git_output(ROOT, "ls-tree", "HEAD", "--", "crosspoint-reader").split()
    if len(pin_entry) != 4 or pin_entry[:2] != ["160000", "commit"]:
        raise ValueError("Missing committed crosspoint-reader submodule pin")
    pin = pin_entry[2]
    print(f"Committed submodule pin: {pin}")
    if pin != revision:
        print("Checkout differs from the committed pin; no pin will be changed.")
    if no_fetch:
        print("Using cached remote refs (--no-fetch); this is not a check of live remote state.")
    # Resolve both remotes before fetching either; never add or guess a remote.
    remotes = [(remote, branch, git_output(SOURCE, "remote", "get-url", remote))
               for remote, branch in UPDATE_BRANCHES]
    changed = pin != revision
    for remote, branch, url in remotes:
        print(f"\n{remote}/{branch} ({url})", flush=True)
        ref = f"refs/remotes/{remote}/{branch}"
        if not no_fetch:
            # Only the remote-tracking ref moves, including after a fork rebase.
            run(["git", "-C", SOURCE, "fetch", "--no-tags", "--no-recurse-submodules",
                 remote, f"+refs/heads/{branch}:{ref}"], dry_run)
        if dry_run:
            print("Would compare commit counts, recent commits, and changed-file summary with the checkout.")
            continue
        tip = git_output(SOURCE, "rev-parse", "--verify", f"{ref}^{{commit}}")
        git_output(SOURCE, "merge-base", revision, tip)  # Reject unrelated histories.
        local_only, incoming = map(int, git_output(
            SOURCE, "rev-list", "--left-right", "--count", f"{revision}...{tip}"
        ).split())
        print(f"Remote tip: {tip}\nCheckout-only commits: {local_only}; incoming commits: {incoming}")
        if incoming:
            changed = True
            print("Incoming commits (up to 20):")
            print(git_output(SOURCE, "log", "--format=%h %s", "-20", f"{revision}..{tip}"))
            print("Incoming diff from the common ancestor (not a merged/rebased candidate):")
            print(git_output(SOURCE, "diff", "--stat=80,50,20", f"{revision}...{tip}"))
            if local_only:
                print("Histories diverge: integrate deliberately; do not reset away checkout-only commits.")
        else:
            print("No incoming commits.")
    print("\nDevice checks still required after a deliberate flash:")
    print("- Boot; open a book; touch/buttons and frontlight; sleep/wake; USB Drive mode.")
    print("- Fonts/styles and dictionary lookup; page turns and saved progress in all orientations.")
    print("- Wi-Fi/Tailscale OPDS downloads; BookOrbit progress, statistics, and highlight export.")
    print("Back up SD state first. This command does not flash or verify the installed device firmware.")
    if verify:
        verify_firmware(revision, dry_run)
    else:
        print("Tests/build NOT RUN. Use --verify for this checkout; rerun after integrating any updates.")
    return False if changed and not dry_run else True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "ports", "build", "flash", "setup", "dictionaries", "updates"))
    parser.add_argument("--no-fetch", action="store_true", help="For updates: compare cached remote refs only")
    parser.add_argument("--verify", action="store_true", help="For updates: test and build the current checkout, not remote tips")
    parser.add_argument("--sd-root", type=Path, help="Mounted CrossPoint SD root for setup or dictionaries")
    parser.add_argument("--check", action="store_true", help="Check SD setup without writes; 0=current, 1=drift, 2=error")
    parser.add_argument("--port", help="Explicit serial device path, required for flash")
    parser.add_argument("--confirm-flash", action="store_true",
                        help="Confirm that this port is your X4 Pro and firmware may be overwritten")
    parser.add_argument("--dry-run", action="store_true", help="Preview without installing, building, or flashing")
    args = parser.parse_args(argv)
    if args.action != "updates" and (args.no_fetch or args.verify):
        parser.error("--no-fetch and --verify apply only to updates")
    if args.action in ("setup", "dictionaries"):
        if args.sd_root is None:
            parser.error(f"{args.action} requires --sd-root")
    elif args.sd_root is not None or args.check:
        parser.error("--sd-root and --check apply only to setup or dictionaries")
    if args.action != "flash" and (args.port or args.confirm_flash):
        parser.error("--port and --confirm-flash apply only to flash")
    if args.action == "flash" and not args.port:
        parser.error("flash requires --port")
    if args.action == "flash" and not args.dry_run and not args.confirm_flash:
        parser.error("flash requires --confirm-flash; back up SD data and verify the device first")
    try:
        if args.action == "updates":
            if not updates(no_fetch=args.no_fetch, verify=args.verify, dry_run=args.dry_run):
                parser.exit(1)
        elif args.action == "setup":
            if not setup(args.sd_root, check=args.check or args.dry_run):
                parser.exit(1)
        elif args.action == "dictionaries":
            result = dictionaries.install(args.sd_root, check=args.check or args.dry_run,
                                          platform="crosspoint")
            if result is False:
                parser.exit(1)
        elif args.action == "prepare":
            prepare(args.dry_run)
        else:
            if args.action != "ports":
                validate_source()
            if not args.dry_run and not executable("pio").is_file():
                raise ValueError("Toolchain missing; run scripts/x4pro.py prepare first")
            run(pio_command(args.action, args.port), args.dry_run)
            if args.action == "build" and not args.dry_run:
                print(f"Firmware: {SOURCE / '.pio/build/x4pro/firmware.bin'}")
    except (ValueError, OSError, KeyError, subprocess.SubprocessError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prepare CrossPoint X4 Pro firmware and dictionaries; explicitly install or flash."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dictionaries

SOURCE = ROOT / "crosspoint-reader"
TOOLCHAIN = ROOT / ".local/crosspoint-toolchain"
REQUIREMENTS = ROOT / "profiles/x4pro/requirements.txt"


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
    else:
        dictionaries.fetch()


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "ports", "build", "flash", "dictionaries"))
    parser.add_argument("--sd-root", type=Path, help="Mounted CrossPoint SD root for dictionaries")
    parser.add_argument("--check", action="store_true", help="Check dictionaries only; 0=current, 1=drift")
    parser.add_argument("--port", help="Explicit serial device path, required for flash")
    parser.add_argument("--confirm-flash", action="store_true",
                        help="Confirm that this port is your X4 Pro and firmware may be overwritten")
    parser.add_argument("--dry-run", action="store_true", help="Preview without installing, building, or flashing")
    args = parser.parse_args(argv)
    if args.action == "dictionaries":
        if args.sd_root is None:
            parser.error("dictionaries requires --sd-root")
    elif args.sd_root is not None or args.check:
        parser.error("--sd-root and --check apply only to dictionaries")
    if args.action != "flash" and (args.port or args.confirm_flash):
        parser.error("--port and --confirm-flash apply only to flash")
    if args.action == "flash" and not args.port:
        parser.error("flash requires --port")
    if args.action == "flash" and not args.dry_run and not args.confirm_flash:
        parser.error("flash requires --confirm-flash; back up SD data and verify the device first")
    try:
        if args.action == "dictionaries":
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

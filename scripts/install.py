#!/usr/bin/env python3
"""Reconcile managed setup files with an existing, mounted KOReader installation."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "main.lua", "_meta.lua", "bin/install-tailscale.sh",
    "bin/start_tailscale.sh", "bin/stop_tailscale.sh", "bin/uninstall-tailscale.sh",
)


def install(target, userspace_proxy=False, backup_root=None, kindle=False, check=False):
    target = Path(target).resolve()
    source = ROOT / "koreader-tailscale"
    if not (target / "plugins").is_dir():
        raise ValueError("Expected an existing KOReader directory containing plugins/")
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("Target must not overlap the source submodule")
    for name in FILES:
        if not (source / name).is_file():
            raise ValueError("Missing plugin source; run git submodule update --init")
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("Plugin submodule must be clean before installation")

    plugin = target / "plugins/tailscale.koplugin"
    settings = target / "settings/tailscale.lua"
    desired = [(source / name, plugin / name, name.endswith(".sh")) for name in FILES]
    if userspace_proxy:
        if settings.exists():
            print("Existing Tailscale settings preserved; the profile is seed-only.")
        else:
            desired.append((ROOT / "profiles/userspace-proxy/tailscale.lua", settings, False))
    launcher = None
    if kindle:
        # The scriptlet uses KMC/KPM, not the separate KUAL launcher protocol.
        if target.name != "koreader" or not (target.parent / "documents").is_dir():
            raise ValueError("--kindle requires <Kindle storage>/koreader and documents/")
        if not (target.parent / "kmc").is_dir():
            raise ValueError("--kindle requires an existing KMC setup (kmc/ directory)")
        launcher = target.parent / "documents/KOReader No Framework.sh"
        desired.append((ROOT / "kindle/documents/KOReader No Framework.sh", launcher, True))

    changed = []
    for original, destination, executable in desired:
        if not original.is_file():
            raise ValueError(f"Missing managed source: {original}")
        # Refuse redirected writes, including symlinked destination directories.
        if any(p.is_symlink() for p in (destination, *destination.parents)):
            raise ValueError(f"Symlinked destination is unsupported: {destination}")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Expected a regular file: {destination}")
        if (not destination.exists()
                or original.read_bytes() != destination.read_bytes()
                or (executable and not destination.stat().st_mode & 0o111)):
            changed.append((original, destination, executable))
    if not changed:
        print(f"Managed files already up to date ({revision}); no writes or backup needed.")
        return None
    for _, destination, _ in changed:
        print(f"{'Would update' if check else 'Update'}: {destination}")
    if check:
        return False

    # Backups contain credentials and device identity: keep local and private.
    backup_root = Path(backup_root) if backup_root else ROOT / ".local/backups"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"),
        dir=backup_root,
    ))
    # Finish every backup before writing anything to the device.
    for relative in ("plugins/tailscale.koplugin", "settings/tailscale.lua", "settings.reader.lua"):
        original = target / relative
        if original.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir():
                shutil.copytree(original, saved)
            else:
                shutil.copy2(original, saved)
    if launcher is not None and launcher.exists():
        saved = backup / "kindle/documents" / launcher.name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(launcher, saved)
    (backup / "installation.txt").write_text(
        f"Target: {target}\nPlugin revision: {revision}\n"
        f"Previous tailscale settings existed: {settings.exists()}\n"
        f"Kindle launcher managed: {kindle}\n"
        f"Previous Kindle launcher existed: {launcher is not None and launcher.exists()}\n"
    )
    print(f"Backup: {backup}", flush=True)

    # Explicit code-only overlay: never delete runtime files or copy repo metadata.
    for original, destination, executable in changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        destination.chmod(0o755 if executable else 0o644)
    print(f"Installed {revision}. Safely eject, restart KOReader, and verify connectivity.")
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("koreader_dir", type=Path, help="Mounted KOReader directory")
    parser.add_argument("--userspace-proxy", action="store_true",
                        help="Seed userspace/proxy settings only if no settings exist")
    parser.add_argument("--kindle", action="store_true",
                        help="Also install the optional KMC no-framework scriptlet")
    parser.add_argument("--check", action="store_true",
                        help="Read-only drift check: exit 0 if current, 1 if updates needed")
    args = parser.parse_args()
    # Restrict permissions on backups, including copied credentials.
    os.umask(0o077)
    try:
        result = install(args.koreader_dir, args.userspace_proxy,
                         kindle=args.kindle, check=args.check)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"Error: {error}\n")
    if result is False:
        parser.exit(1)

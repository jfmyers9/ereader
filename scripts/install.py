#!/usr/bin/env python3
"""Overlay pinned Tailscale plugin code onto an existing KOReader installation."""

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


def install(target, userspace_proxy=False, backup_root=None):
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

    # Backups contain credentials and device identity: keep local and private.
    backup_root = Path(backup_root) if backup_root else ROOT / ".local/backups"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"),
        dir=backup_root,
    ))
    plugin = target / "plugins/tailscale.koplugin"
    settings = target / "settings/tailscale.lua"
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
    (backup / "installation.txt").write_text(
        f"Target: {target}\nPlugin revision: {revision}\n"
        f"Previous tailscale settings existed: {settings.exists()}\n"
    )
    print(f"Backup: {backup}", flush=True)

    # Explicit code-only overlay: never delete runtime files or copy repo metadata.
    for name in FILES:
        destination = plugin / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
        destination.chmod(0o755 if name.endswith(".sh") else 0o644)
    if userspace_proxy:
        if settings.exists():
            print("Existing settings preserved; enable Force userspace mode and "
                  "Automatically configure HTTP proxy in the plugin menu.")
        else:
            settings.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "profiles/userspace-proxy/tailscale.lua", settings)
    print(f"Installed {revision}. Safely eject, restart KOReader, and verify connectivity.")
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("koreader_dir", type=Path, help="Mounted KOReader directory")
    parser.add_argument("--userspace-proxy", action="store_true",
                        help="Seed userspace/proxy settings only if no settings exist")
    args = parser.parse_args()
    # Restrict permissions on backups, including copied credentials.
    os.umask(0o077)
    install(args.koreader_dir, args.userspace_proxy)

#!/usr/bin/env python3
"""Reconcile managed setup files with an existing, mounted KOReader installation."""

import argparse
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FILES = (
    "main.lua", "_meta.lua", "bin/install-tailscale.sh",
    "bin/start_tailscale.sh", "bin/stop_tailscale.sh", "bin/uninstall-tailscale.sh",
)


def install(target, userspace_proxy=False, backup_root=None, kindle=False, check=False, bookorbit=False,
            settings_profile=None):
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
    if bookorbit:
        from scripts import bookorbit as bookorbit_source

        bookorbit_dir = target / "plugins/bookorbit.koplugin"
        if (bookorbit_dir / bookorbit_source.PROVISION).exists():
            raise ValueError("Existing BookOrbit provisioning file requires manual review before installation")
        desired.extend((path, bookorbit_dir / name, False)
                       for name, path in bookorbit_source.prepared_files())
    if userspace_proxy and settings_profile != "kindle":
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
    settings_changes = []
    if settings_profile:
        from scripts import settings as settings_source

        settings_changes = settings_source.plan(target, settings_profile)
    if not changed and not settings_changes:
        print(f"Managed files already up to date ({revision}); no writes or backup needed.")
        return None
    for _, destination, _ in changed:
        print(f"{'Would update' if check else 'Update'}: {destination}")
    for change in settings_changes:
        print(f"{'Would merge' if check else 'Merge'}: {change.path}")
        for key in change.keys:
            print(f"  {key}")
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
    backup_paths = ["plugins/tailscale.koplugin", "settings/tailscale.lua", "settings.reader.lua"]
    if bookorbit:
        backup_paths += ["plugins/bookorbit.koplugin", "settings/bookorbit_sync_state.lua"]
    backup_paths += [change.path.relative_to(target).as_posix() for change in settings_changes]
    for relative in dict.fromkeys(backup_paths):
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
        f"BookOrbit managed: {bookorbit}\n"
        f"Settings profile: {settings_profile or 'none'}\n"
    )
    if settings_changes:
        (backup / "settings-profile-files.json").write_text(json.dumps({
            change.path.relative_to(target).as_posix(): {"existed": change.before is not None}
            for change in settings_changes
        }, indent=2) + "\n")
    print(f"Backup: {backup}", flush=True)

    for change in settings_changes:
        change.verify_unchanged()
    # Explicit code-only overlay: never delete runtime files or copy repo metadata.
    for original, destination, executable in changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        destination.chmod(0o755 if executable else 0o644)
    for change in settings_changes:
        change.write()
    print(f"Installed {revision}. Safely eject, restart KOReader, and verify connectivity.")
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("koreader_dir", type=Path, help="Mounted KOReader directory")
    parser.add_argument("--userspace-proxy", action="store_true",
                        help="Seed userspace/proxy settings only if no settings exist")
    parser.add_argument("--kindle", action="store_true",
                        help="Also install the KMC scriptlet; with --fonts, rely on system Bookerly")
    parser.add_argument("--bookorbit", action="store_true",
                        help="Also install the verified public BookOrbit plugin pin (fetch first)")
    parser.add_argument("--dictionaries", action="store_true",
                        help="Also install cached dictionary pins and preferences (dictionaries.py fetch first)")
    parser.add_argument("--fonts", action="store_true",
                        help="Install cached fonts; Kindle flag/profile skips system-provided Bookerly")
    parser.add_argument("--settings-profile", choices=("shared", "kindle"),
                        help="Merge curated preferences; kindle includes shared defaults (requires LuaJIT)")
    parser.add_argument("--check", action="store_true",
                        help="Read-only drift check: exit 0 if current, 1 if updates needed")
    args = parser.parse_args(argv)
    # Restrict permissions on backups, including copied credentials.
    os.umask(0o077)
    try:
        font_result = None
        font_device = "kindle" if args.kindle or args.settings_profile == "kindle" else "koreader"
        if args.fonts:
            from scripts import fonts

            # Fail on missing fonts before touching plugins or settings.
            font_result = fonts.install(args.koreader_dir, device=font_device, check=True)
        dictionary_result = None
        if args.dictionaries:
            from scripts import dictionaries

            # Validate archives and settings before any plugin writes. Dictionaries
            # are a separate backed-up transaction, not an all-or-nothing bundle.
            dictionary_result = dictionaries.install(args.koreader_dir, check=True)
        result = install(args.koreader_dir, args.userspace_proxy,
                         kindle=args.kindle, check=args.check, bookorbit=args.bookorbit,
                         settings_profile=args.settings_profile)
        if args.dictionaries and not args.check:
            dictionaries.install(args.koreader_dir)
            dictionary_result = None
        if args.fonts and not args.check:
            fonts.install(args.koreader_dir, device=font_device)
            font_result = None
    except (ValueError, OSError, KeyError, subprocess.SubprocessError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Error: {error}\n")
    if result is False or dictionary_result is False or font_result is False:
        parser.exit(1)


if __name__ == "__main__":
    main()

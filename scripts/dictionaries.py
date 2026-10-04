#!/usr/bin/env python3
"""Fetch pinned dictionaries and install them over USB for KOReader or CrossPoint."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import settings

LOCK = ROOT / "profiles/dictionaries.json"
CACHE = ROOT / ".local/dictionaries"
PROFILE = ROOT / "profiles/koreader/dictionaries/settings.reader.lua"
MAX_SIZE = 1024 * 1024 * 1024


def digest(path):
    if not path.exists():
        return None
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def pins():
    data = json.loads(LOCK.read_text())
    if data.get("schema") != 1 or not data.get("dictionaries"):
        raise ValueError("Unsupported dictionary lock")
    seen = set()
    for pin in data["dictionaries"]:
        if not re.fullmatch(r"[a-z0-9-]+", pin["id"]) or pin["id"] in seen:
            raise ValueError("Invalid or duplicate dictionary ID")
        seen.add(pin["id"])
        if not re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]):
            raise ValueError("Invalid dictionary checksum")
        if not re.fullmatch(r"[\w.-]+\.(zip|tar.gz)", pin["archive"]):
            raise ValueError("Invalid archive filename")
        if not pin["url"].startswith("https://"):
            raise ValueError("Dictionary downloads require HTTPS")
        if not re.fullmatch(r"[a-z]+-[a-z]+", pin["language"]):
            raise ValueError("Invalid dictionary language")
        if pin["prefix"] and not re.fullmatch(r"[\w-]+/", pin["prefix"]):
            raise ValueError("Invalid archive prefix")
        members = pin["members"]
        if (not members or len(set(members)) != len(members)
                or any(not re.fullmatch(r"[\w.-]+\.(ifo|idx|syn|dict.dz)", n) for n in members)
                or sum(n.endswith(".ifo") for n in members) != 1):
            raise ValueError("Invalid dictionary members")
    return data["dictionaries"]


def archive_path(pin):
    return CACHE / pin["sha256"] / pin["archive"]


def verify(path, pin):
    if digest(path) != pin["sha256"]:
        raise ValueError(f"{pin['id']}: archive checksum mismatch or missing file. "
                         "Fetch the pinned archive; changed upstream snapshots require explicit review.")


def fetch(from_dir=None):
    """Reuse verified cache offline; optionally import previously downloaded archives."""
    for pin in pins():
        destination = archive_path(pin)
        if destination.exists():
            verify(destination, pin)
            print(f"Cached: {pin['id']}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=destination.parent, suffix=".part")
        os.close(descriptor)
        temporary = Path(name)
        try:
            if from_dir is not None:
                shutil.copyfile(Path(from_dir) / pin["archive"], temporary)
            else:
                subprocess.run([
                    "curl", "--fail", "--location", "--proto", "=https",
                    "--proto-redir", "=https", "--retry", "3", "--connect-timeout", "20",
                    "--max-time", "600", "--output", str(temporary), pin["url"],
                ], check=True)
            verify(temporary, pin)
            os.replace(temporary, destination)
            print(f"Fetched and verified: {pin['id']}")
        finally:
            temporary.unlink(missing_ok=True)


def extract(pin, destination):
    """Extract only pinned regular files; never trust archive paths or links."""
    path = archive_path(pin)
    verify(path, pin)
    expected = {pin["prefix"] + name: name for name in pin["members"]}
    seen = set()
    total = 0

    def copy(name, size, stream):
        nonlocal total
        if name not in expected or name in seen:
            raise ValueError(f"Unexpected or duplicate dictionary member: {name}")
        total += size
        if size < 0 or total > MAX_SIZE:
            raise ValueError("Dictionary archive is too large")
        seen.add(name)
        with (destination / expected[name]).open("wb") as output:
            shutil.copyfileobj(stream, output)

    destination.mkdir(parents=True)
    if pin["archive"].endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG):
                    raise ValueError("Non-regular dictionary archive member")
                with archive.open(info) as stream:
                    copy(info.filename, info.file_size, stream)
    else:
        with tarfile.open(path, "r:gz") as archive:
            for info in archive:
                if info.isdir() and info.name.rstrip("/") == pin["prefix"].rstrip("/"):
                    continue
                if not info.isfile():
                    raise ValueError("Non-regular dictionary archive member")
                with archive.extractfile(info) as stream:
                    copy(info.name, info.size, stream)
    if seen != set(expected):
        raise ValueError("Missing dictionary archive members")
    ifo = next(destination.glob("*.ifo"))
    content = ifo.read_text()
    metadata = dict(line.split("=", 1) for line in content.splitlines() if "=" in line)
    base = ifo.with_suffix("")
    if (not content.startswith("StarDict's dict ifo file\n")
            or metadata.get("bookname") != pin["name"]
            or not base.with_suffix(base.suffix + ".dict.dz").is_file()
            or (destination / (ifo.stem + ".idx")).stat().st_size != int(metadata["idxfilesize"])):
        raise ValueError("Invalid StarDict metadata or missing data")
    # KOReader adds this metadata during its own downloads; do the same for USB installs.
    content = "\n".join(line for line in content.splitlines() if not line.startswith("lang="))
    ifo.write_text(content + f"\nlang={pin['language']}\n")


def safe_destination(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"Symlinked destination is unsupported: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")


def crosspoint_folder(target, pin, prepared):
    """Reject layouts that CrossPoint would skip or resolve to unmanaged data."""
    folder = target / "dictionaries" / pin["id"]
    safe_destination(folder / ".ereader-probe")
    if folder.exists() and not folder.is_dir():
        raise ValueError(f"Expected a dictionary directory: {folder}")
    hidden = target / ".dictionaries" / pin["id"]
    safe_destination(hidden / ".ereader-probe")
    if hidden.exists():
        raise ValueError(f"Conflicting hidden dictionary; rename it before installing: {hidden}")
    ifo = next(prepared.glob("*.ifo"))
    metadata = dict(line.split("=", 1) for line in ifo.read_text().splitlines() if "=" in line)
    if metadata.get("idxoffsetbits", "32") != "32":
        raise ValueError("CrossPoint requires 32-bit dictionary index offsets")
    indexes = [name for name in pin["members"] if name.endswith(".idx")]
    if indexes != [ifo.stem + ".idx"]:
        raise ValueError("CrossPoint requires one dictionary index per folder")
    plain = folder / (ifo.stem + ".dict")
    safe_destination(plain)
    if plain.exists():
        raise ValueError(f"Unmanaged plain dictionary would shadow pinned data: {plain}")
    if folder.exists():
        for entry in folder.glob("*.idx"):
            if not entry.name.startswith("._") and entry.name not in indexes:
                raise ValueError(f"Conflicting dictionary index: {entry}")
    return folder


def install(target, check=False, preferences=True, backup_root=None, platform="koreader"):
    if platform not in ("koreader", "crosspoint"):
        raise ValueError("Unsupported dictionary platform")
    target = Path(target).absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("Symlinked target is unsupported")
    target = target.resolve()
    if platform == "crosspoint":
        safe_destination(target / ".crosspoint" / ".ereader-probe")
        if not target.is_dir() or not (target / ".crosspoint").is_dir():
            raise ValueError("Expected a CrossPoint SD root containing .crosspoint/; use USB Drive mode")
        preferences = False
    elif not (target / "plugins").is_dir():
        raise ValueError("Expected an existing KOReader directory containing plugins/")
    lock = pins()
    with tempfile.TemporaryDirectory(prefix="ereader-dictionaries-") as temporary:
        changes = []
        for pin in lock:
            prepared = Path(temporary) / pin["id"]
            extract(pin, prepared)
            folder = (crosspoint_folder(target, pin, prepared) if platform == "crosspoint"
                      else target / "data/dict" / pin["id"])
            for name in pin["members"]:
                source = prepared / name
                destination = folder / name
                safe_destination(destination)
                before = digest(destination)
                if before != digest(source):
                    # CrossPoint caches only source sizes: same-size edits need invalidation too.
                    if name.endswith((".idx", ".syn")):
                        if platform == "crosspoint":
                            suffixes = (".qidx", ".sidx") if name.endswith(".idx") else (".sidx",)
                            caches = [destination.with_suffix(suffix) for suffix in suffixes]
                        else:
                            caches = [destination.with_name(destination.name + ".oft")]
                        for cache in caches:
                            safe_destination(cache)
                            if cache.exists() and not any(d == cache for _, d, _ in changes):
                                changes.append((None, cache, digest(cache)))
                    # Remove caches before replacing the index: interrupted installs must
                    # never leave a new same-size index paired with stale lookup offsets.
                    changes.append((source, destination, before))
        merges = settings.plan_files(target, {"settings.reader.lua": [PROFILE]}) if preferences else []
        if not changes and not merges:
            print("Dictionaries and requested preferences are current; no writes or backup needed.")
            return None
        for source, destination, _ in changes:
            print(f"{'Would ' if check else ''}{'update' if source else 'invalidate'}: {destination}")
        for change in merges:
            print(f"{'Would merge' if check else 'Merge'}: {change.path} (dictionary preferences only)")
        if check:
            return False
        backup_root = Path(backup_root or ROOT / ".local/backups").resolve()
        if backup_root.is_relative_to(target if platform == "crosspoint" else target.parent):
            raise ValueError("Dictionary backups must be outside the device storage")
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = Path(tempfile.mkdtemp(
            prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-dictionaries-"), dir=backup_root))
        all_paths = [d for _, d, _ in changes] + [c.path for c in merges]
        manifest = {}
        for destination in all_paths:
            relative = destination.relative_to(target)
            manifest[str(relative)] = {"existed": destination.exists()}
            if destination.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(destination, saved)
                saved.chmod(0o600)
        (backup / "dictionary-files.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (backup / "dictionaries.json").write_text(json.dumps(lock, indent=2) + "\n")
        print(f"Backup: {backup}", flush=True)
        for _, destination, before in changes:
            safe_destination(destination)
            if digest(destination) != before:
                raise ValueError("Dictionary changed since planning; stop device access and retry")
        for change in merges:
            change.verify_unchanged()
        for source, destination, before in changes:
            safe_destination(destination)
            if digest(destination) != before:
                raise ValueError("Dictionary changed during installation; restore backup and retry")
            if source is None:
                destination.unlink()
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".ereader-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as stream:
                    shutil.copyfileobj(stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(name, 0o644)
                os.replace(name, destination)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        for change in merges:
            change.write()
        if platform == "crosspoint":
            print("Installed dictionaries. Safely eject, leave USB Drive mode, and select one in "
                  "Settings > Reader > Dictionary.")
        else:
            print("Installed dictionaries. Safely eject and restart KOReader.")
        return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch", help="Download and verify committed pins on the host")
    fetch_parser.add_argument("--from-dir", type=Path, help="Import named archives offline instead")
    install_parser = commands.add_parser("install", help="Install cached pins; quit KOReader first")
    install_parser.add_argument("koreader_dir", type=Path)
    install_parser.add_argument("--check", action="store_true", help="No device writes; 0=current, 1=drift")
    install_parser.add_argument("--no-preferences", action="store_true",
                                help="Do not merge dictionary order and language presets (otherwise requires LuaJIT)")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "fetch":
            fetch(args.from_dir)
        elif install(args.koreader_dir, args.check, not args.no_preferences) is False:
            parser.exit(1)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError,
            tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(2, f"Error: {error}\n")

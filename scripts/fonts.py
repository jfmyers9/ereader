#!/usr/bin/env python3
"""Prepare one private font library and reconcile it onto KOReader or X4 Pro."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".local/fonts"
LOCK = ROOT / "profiles/fonts.json"
AMAZON_PREFIX = "Amazon_Typefaces_Complete_Font_Set_Mar2020/"
AMAZON_GUIDELINES = "Amazon Ember Licensing Guidelines.pdf"


def profile():
    return json.loads(LOCK.read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_ttf(data, family, style):
    """Check static TrueType structure, family identity and actual style bits."""
    try:
        if data[:4] != b"\x00\x01\x00\x00":
            raise ValueError("Expected a TrueType font")
        count = struct.unpack_from(">H", data, 4)[0]
        tables = {}
        for index in range(count):
            tag, _, offset, size = struct.unpack_from(">4sIII", data, 12 + 16 * index)
            if offset + size > len(data):
                raise ValueError("Truncated font table")
            tables[tag] = data[offset:offset + size]
        if b"fvar" in tables:
            raise ValueError("Use static TTFs, not variable fonts")
        if not all(tag in tables for tag in (b"glyf", b"loca", b"cmap", b"head", b"name")):
            raise ValueError("Missing required TrueType tables")
        bits = struct.unpack_from(">H", tables[b"head"], 44)[0] & 3
        expected = (1 if "Bold" in style else 0) | (2 if "Italic" in style else 0)
        if bits != expected:
            raise ValueError(f"Incorrect style; expected {style}")
        names = tables[b"name"]
        _, count, start = struct.unpack_from(">HHH", names)
        families = set()
        for index in range(count):
            platform, _, _, name_id, length, offset = struct.unpack_from(">6H", names, 6 + 12 * index)
            if name_id in (1, 16) and platform in (0, 3):
                families.add(names[start + offset:start + offset + length].decode("utf-16-be"))
        if family not in families:
            raise ValueError(f"Expected font family {family}, found {sorted(families)}")
    except (struct.error, UnicodeError) as error:
        raise ValueError("Malformed TrueType font") from error


def save_family(family, files, source, upgrade_import=False):
    destination = CACHE / family
    # Cache replacement is explicit, never silently repin an existing import.
    if destination.exists():
        existing = prepared_files([family])
        previous = {p.name: p.read_bytes() for _, p in existing}
        if upgrade_import and all(files.get(name) == data for name, data in previous.items()):
            # Only adopt official provenance after a verified download matches every
            # imported byte. Never overwrite a different private font version.
            manifest = {"source": source, "files": {name: sha(data) for name, data in files.items()}}
            for name, data in files.items():
                if name not in previous:
                    (destination / name).write_bytes(data)
            fd, temporary = tempfile.mkstemp(dir=destination, suffix=".part")
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(json.dumps(manifest, indent=2) + "\n")
                os.replace(temporary, destination / "manifest.json")
            finally:
                Path(temporary).unlink(missing_ok=True)
            print(f"Verified imported {family} against official release; preserved guidelines.")
            return
        if previous == files:
            print(f"Cached: {family}")
            return
        raise ValueError(f"Different cached {family}; move {destination} aside before replacing it")
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as directory:
        stage = Path(directory) / family
        stage.mkdir()
        for name, data in files.items():
            (stage / name).write_bytes(data)
        (stage / "manifest.json").write_text(json.dumps({
            "source": source,
            "files": {name: sha(data) for name, data in files.items()},
        }, indent=2) + "\n")
        stage.rename(destination)
    print(f"Prepared: {family}")


def fetch_family(family):
    pin = profile()[family.lower()]
    upgrade_import = False
    if (CACHE / family).exists():
        prepared_files([family])
        meta = json.loads((CACHE / family / "manifest.json").read_text())
        upgrade_import = family == "Bookerly" and isinstance(meta["source"], str)
        if not upgrade_import:
            print(f"Cached: {family}")
            return
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as directory:
        archive = Path(directory) / "fonts.zip"
        subprocess.run([
            "curl", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
            "--connect-timeout", "20", "--max-time", "180", "--output", str(archive), pin["url"],
        ], check=True)
        if sha(archive.read_bytes()) != pin["sha256"]:
            raise ValueError(f"{family} archive checksum mismatch")
        files = {}
        with zipfile.ZipFile(archive) as bundle:
            for style in profile()["styles"]:
                name = f"{family}-{style}.ttf"
                prefix = "fonts/ttf/" if family == "Literata" else AMAZON_PREFIX + "Bookerly/"
                data = bundle.read(prefix + name)
                validate_ttf(data, family, style)
                files[name] = data
            if family == "Literata":
                files["OFL.txt"] = bundle.read("OFL.txt")
            else:
                files[AMAZON_GUIDELINES] = bundle.read(AMAZON_PREFIX + AMAZON_GUIDELINES)
        save_family(family, files, pin, upgrade_import=upgrade_import)


def fetch_literata():
    fetch_family("Literata")


def import_bookerly(directory):
    """Optional offline import of user-supplied fonts instead of the official download."""
    files = {}
    for style in profile()["styles"]:
        name = f"Bookerly-{style}.ttf"
        data = (Path(directory) / name).read_bytes()
        validate_ttf(data, "Bookerly", style)
        files[name] = data
    save_family("Bookerly", files, "User-supplied private copy; no redistribution grant recorded")


def prepared_files(families=None):
    config = profile()
    result = []
    for family in families or config["families"]:
        if family not in config["families"]:
            raise ValueError(f"Unknown font family: {family}")
        base = CACHE / family
        manifest = base / "manifest.json"
        if not manifest.is_file():
            raise ValueError(f"Missing {family}; run fonts.py fetch")
        meta = json.loads(manifest.read_text())
        imported = family == "Bookerly" and isinstance(meta["source"], str)
        if not imported and meta["source"] != config[family.lower()]:
            raise ValueError(f"Cached {family} does not match the pinned release")
        names = [f"{family}-{style}.ttf" for style in config["styles"]]
        if family == "Literata":
            names.append("OFL.txt")
        elif not imported:
            names.append(AMAZON_GUIDELINES)
        if set(meta["files"]) != set(names):
            raise ValueError(f"Incomplete {family} manifest")
        for name in names:
            path = base / name
            if not path.is_file() or sha(path.read_bytes()) != meta["files"][name]:
                raise ValueError(f"Missing or corrupted cached font: {path}")
            if name.endswith(".ttf"):
                validate_ttf(path.read_bytes(), family, name[len(family) + 1:-4])
            result.append((Path(family) / name, path))
    return result


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"Symlinked font destination is unsupported: {path}")


def install(target, device="koreader", check=False, families=None, backup_root=None):
    target = Path(target).absolute()
    # macOS /var is itself a symlink; resolve the mount root, not paths inside it.
    if target.is_symlink():
        raise ValueError("Target must not be a symlink")
    target = target.resolve()
    if device not in ("koreader", "x4pro"):
        raise ValueError("Expected koreader or x4pro")
    if not target.is_dir() or (device == "koreader" and not (target / "plugins").is_dir()):
        raise ValueError("Expected an existing KOReader directory (plugins/) or mounted X4 Pro SD root")
    sources = prepared_files(families)
    font_root = target / ("fonts" if device == "koreader" else ".fonts")
    if font_root.is_relative_to(CACHE.resolve()) or CACHE.resolve().is_relative_to(font_root):
        raise ValueError("Font destination overlaps the source cache")
    changes = []
    for relative, source in sources:
        destination = font_root / relative
        safe_path(destination)
        for parent in destination.parents:
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"Expected a directory: {parent}")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Expected a regular file: {destination}")
        if device == "x4pro":
            # CrossPoint gives cpfont precedence, even alongside valid TTFs.
            if any(p.suffix.lower() == ".cpfont" for p in destination.parent.glob("*")):
                raise ValueError(f"Move conflicting .cpfont files out of {destination.parent} first")
            if any(p.stem.casefold() == relative.parts[0].casefold()
                   and p.suffix.lower() in (".ttf", ".otf", ".ttc") for p in font_root.glob("*")):
                raise ValueError(f"Move conflicting loose {relative.parts[0]} fonts out of {font_root} first")
        before = destination.read_bytes() if destination.exists() else None
        after = source.read_bytes()
        if before != after:
            changes.append((destination, before, after))
    for destination, _, _ in changes:
        print(f"{'Would update' if check else 'Update'}: {destination}")
    if not changes:
        print("Managed fonts are current.")
        return True
    if check:
        return False
    backup_root = Path(backup_root) if backup_root else ROOT / ".local/backups"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("fonts-%Y%m%dT%H%M%SZ-"),
                                   dir=backup_root))
    records = {}
    for destination, before, _ in changes:
        relative = destination.relative_to(target)
        records[str(relative)] = {"existed": before is not None}
        if before is not None:
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)
    (backup / "fonts.json").write_text(json.dumps({"target": str(target), "files": records}, indent=2) + "\n")
    print(f"Backup: {backup}")
    for destination, before, after in changes:
        safe_path(destination)
        if (destination.read_bytes() if destination.exists() else None) != before:
            raise ValueError(f"Destination changed during installation: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=destination.parent, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(after)
            os.chmod(name, 0o644)
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
    print("Fonts installed. Safely eject and restart the reader; select the family in its font menu.")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("fetch", help="Fetch both pinned official releases; reuse verified cache offline")
    commands.add_parser("fetch-bookerly", help="Fetch Amazon's official fonts and bundled usage guidelines")
    commands.add_parser("fetch-literata", help="Fetch and verify the official static TTF release")
    importer = commands.add_parser("import-bookerly", help="Privately cache four user-supplied static TTFs")
    importer.add_argument("directory", type=Path)
    installer = commands.add_parser("install", help="Offline install; both families required by default")
    installer.add_argument("target", type=Path)
    installer.add_argument("--device", choices=("koreader", "x4pro"), required=True)
    installer.add_argument("--family", choices=("Bookerly", "Literata"), action="append",
                           help="Explicit partial install instead of the guaranteed two-family set")
    installer.add_argument("--check", action="store_true", help="Read-only: exit 1 for drift, 2 for errors")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "fetch":
            for family in profile()["families"]:
                fetch_family(family)
        elif args.action in ("fetch-literata", "fetch-bookerly"):
            fetch_family(args.action.removeprefix("fetch-").capitalize())
        elif args.action == "import-bookerly":
            import_bookerly(args.directory)
        elif not install(args.target, args.device, args.check, args.family):
            parser.exit(1)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

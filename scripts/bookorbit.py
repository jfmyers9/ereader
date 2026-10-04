#!/usr/bin/env python3
"""Pin and fetch the public, unconfigured BookOrbit KOReader plugin."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "profiles/bookorbit.json"
CACHE = ROOT / ".local/bookorbit"
UPSTREAM = "https://github.com/bookorbit/bookorbit.git"
PLUGIN = "koreader-plugin/bookorbit.koplugin"
PROVISION = "bookorbit_provision.lua"


def content_hash(files):
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def archive_files(data):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("Unsafe path in plugin archive")
            if member.isdir():
                continue
            if not member.isfile() or str(path) != name or name in files:
                raise ValueError("Plugin archive must contain unique regular files")
            if path.name == PROVISION:
                raise ValueError("Refusing a credential-provisioning file")
            files[name] = archive.extractfile(member).read()
    if not {"main.lua", "_meta.lua"}.issubset(files):
        raise ValueError("Archive is missing required plugin files")
    return files


def git(*args):
    return subprocess.check_output(["git", "-C", str(CACHE / "source.git"), *args])


def download(ref):
    CACHE.mkdir(parents=True, exist_ok=True)
    repository = CACHE / "source.git"
    if not repository.exists():
        subprocess.run(["git", "init", "--bare", str(repository)], check=True,
                       stdout=subprocess.DEVNULL)
    # Fixed public source: no homelab access, Docker daemon, or deployment credentials.
    git("fetch", "--depth=1", UPSTREAM, ref)
    revision = git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
    return revision, archive_files(git("archive", f"{revision}:{PLUGIN}"))


def read_lock():
    lock = json.loads(LOCK.read_text())
    if (lock.get("schema") != 1
            or not re.fullmatch(r"[0-9a-f]{40}", lock.get("revision", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", lock.get("sha256", ""))
            or not re.fullmatch(r"\d+\.\d+\.\d+", lock.get("release", ""))):
        raise ValueError("Invalid BookOrbit lock file")
    return lock


def store_files(revision, files):
    destination = CACHE / revision / "bookorbit.koplugin"
    if destination.exists():
        # Do not overwrite a modified cache silently.
        if content_hash(cached_files(destination)) != content_hash(files):
            raise ValueError("Existing BookOrbit cache differs; inspect it before replacing")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "plugin"
        staged.mkdir()
        for name, data in files.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        staged.rename(destination)
    return destination


def cached_files(directory):
    if directory.is_symlink():
        raise ValueError("Symlinked plugin cache is unsupported")
    files = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinked plugin cache is unsupported")
        if path.is_file():
            if path.name == PROVISION:
                raise ValueError("Refusing a credential-provisioning file")
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def prepared_files():
    lock = read_lock()
    directory = CACHE / lock["revision"] / "bookorbit.koplugin"
    if not directory.is_dir():
        raise ValueError("BookOrbit not cached; run python3 scripts/bookorbit.py fetch")
    files = cached_files(directory)
    if content_hash(files) != lock["sha256"]:
        raise ValueError("BookOrbit cache checksum mismatch; refusing installation")
    return [(name, directory / name) for name in sorted(files)]


def fetch():
    lock = read_lock()
    directory = CACHE / lock["revision"] / "bookorbit.koplugin"
    if directory.exists():
        prepared_files()
        print("Pinned BookOrbit plugin already cached and verified.")
        return
    revision, files = download(lock["revision"])
    if revision != lock["revision"] or content_hash(files) != lock["sha256"]:
        raise ValueError("Downloaded BookOrbit plugin does not match the lock")
    store_files(revision, files)
    print(f"Cached BookOrbit {lock['release']} at {revision}.")


def pin(release):
    if not re.fullmatch(r"\d+\.\d+\.\d+", release):
        raise ValueError("Use an explicit release such as 3.2.0, not latest or an image URL")
    revision, files = download(f"refs/tags/v{release}")
    store_files(revision, files)
    lock = {"schema": 1, "release": release, "revision": revision, "sha256": content_hash(files)}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"Pinned BookOrbit {release}; review the diff in {LOCK.relative_to(ROOT)}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("fetch", help="Fetch/verify the committed pin without changing it")
    sub.add_parser("pin", help="Explicitly update the public lock").add_argument("release")
    args = parser.parse_args()
    try:
        fetch() if args.action == "fetch" else pin(args.release)
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

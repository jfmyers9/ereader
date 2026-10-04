"""Plan curated Lua settings merges without writing to the device."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "profiles/koreader"
ALLOWED_FILES = {"settings.reader.lua", "settings/gestures.lua", "settings/tailscale.lua"}


@dataclass
class Change:
    path: Path
    before: bytes | None
    content: bytes
    keys: list[str]

    def verify_unchanged(self):
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.before:
            raise ValueError("Settings changed since planning; quit KOReader and retry")

    def write(self):
        self.verify_unchanged()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Replace complete files atomically, rather than truncating live credentials.
        descriptor, temporary = tempfile.mkstemp(prefix=".ereader-settings-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def plan(target, profile):
    if profile not in ("shared", "kindle"):
        raise ValueError("Unknown settings profile")
    lua = shutil.which("luajit")
    if not lua:
        raise ValueError("Settings profiles require host LuaJIT (macOS: brew install luajit)")
    layers = ["shared"] + (["kindle"] if profile == "kindle" else [])
    rules = {}
    for layer in layers:
        directory = PROFILE_ROOT / layer
        if not directory.is_dir():
            raise ValueError(f"Missing settings profile: {layer}")
        for source in sorted(directory.rglob("*.lua")):
            relative = source.relative_to(directory).as_posix()
            if relative not in ALLOWED_FILES:
                raise ValueError(f"Unsupported settings destination: {relative}")
            rules.setdefault(relative, []).append(source)
    changes = []
    for relative, sources in sorted(rules.items()):
        destination = target / relative
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise ValueError("Symlinked settings destination is unsupported")
        if destination.exists() and not destination.is_file():
            raise ValueError("Settings destination must be a regular file")
        before = destination.read_bytes() if destination.exists() else None
        if before is None and destination.with_suffix(destination.suffix + ".old").exists():
            raise ValueError(f"Restore {relative}.old before applying profiles to missing settings")
        result = subprocess.run(
            [lua, str(ROOT / "scripts/merge_settings.lua"), str(destination),
             "missing" if before is None else "existing", *map(str, sources)],
            capture_output=True, timeout=10,
        )
        if result.returncode:
            raise ValueError(f"Cannot safely merge {relative}; original settings preserved")
        if result.stdout:
            changes.append(Change(destination, before, result.stdout, result.stderr.decode().splitlines()))
    return changes

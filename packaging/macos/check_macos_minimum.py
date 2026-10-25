# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Fail the DMG build when a bundled Mach-O needs a newer macOS than the app declares.

``build-dmg.sh`` runs this against the frozen ``.app``. Every Mach-O's minimum
(``minos`` of ``LC_BUILD_VERSION``, or ``LC_VERSION_MIN_MACOSX`` on older
binaries) must not exceed the Info.plist's ``LSMinimumSystemVersion``: on an
older macOS the loader refuses such a library and the app quits at launch
without a message.

    python check_macos_minimum.py dist/OpenFollow.app
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

# Thin and universal headers, 32- and 64-bit, in either byte order.
_MACHO_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)

# (load command, field) pairs that carry a minimum macOS version. The field is
# matched per command: LC_BUILD_VERSION's tool entries also print "version".
_MINIMUM_FIELDS = frozenset({("LC_BUILD_VERSION", "minos"), ("LC_VERSION_MIN_MACOSX", "version")})

Version = tuple[int, ...]


def parse_version(text: str) -> Version:
    """``"15.0"`` -> ``(15,)``: trailing zeros dropped so ``15`` equals ``15.0.0``."""
    parts = [int(part) for part in text.strip().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in (version if len(version) > 1 else (*version, 0)))


def minimum_from_load_commands(otool_output: str) -> Version | None:
    """The newest minimum an ``otool -l`` listing declares, or None when it names none."""
    found: list[Version] = []
    command = ""
    for line in otool_output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        if fields[0] == "cmd":
            command = fields[1]
        elif (command, fields[0]) in _MINIMUM_FIELDS:
            found.append(parse_version(fields[1]))
    return max(found, default=None)


def declared_minimum(app: Path) -> Version:
    """``LSMinimumSystemVersion`` from the app's Info.plist; KeyError when it has none."""
    with (app / "Contents" / "Info.plist").open("rb") as plist:
        return parse_version(str(plistlib.load(plist)["LSMinimumSystemVersion"]))


def macho_files(root: Path) -> Iterator[Path]:
    """Every Mach-O under ``root``, symlinks skipped so a file is read once."""
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) in _MACHO_MAGICS:
                yield path


def _otool_load_commands(path: Path) -> str:
    return subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True).stdout


def bundle_minimums(
    app: Path,
    load_commands: Callable[[Path], str] = _otool_load_commands,
) -> list[tuple[Path, Version]]:
    """Each bundled Mach-O that declares a minimum macOS, with that minimum."""
    minimums = []
    for path in macho_files(app):
        needed = minimum_from_load_commands(load_commands(path))
        if needed is not None:
            minimums.append((path, needed))
    return minimums


def main(argv: list[str], load_commands: Callable[[Path], str] = _otool_load_commands) -> int:
    if len(argv) != 1:
        print("usage: check_macos_minimum.py <path/to/OpenFollow.app>", file=sys.stderr)
        return 2
    app = Path(argv[0])
    try:
        minimum = declared_minimum(app)
    except KeyError:
        print(f"{app}: Info.plist declares no LSMinimumSystemVersion.", file=sys.stderr)
        return 1
    minimums = bundle_minimums(app, load_commands)
    too_new = [(path, needed) for path, needed in minimums if needed > minimum]
    if not too_new:
        print(f"Every bundled Mach-O runs on macOS {format_version(minimum)}.")
        if minimums:
            # A plist declaring more than any library needs shows here.
            path, needed = max(minimums, key=lambda entry: entry[1])
            print(f"Newest minimum among them: macOS {format_version(needed)} ({path.relative_to(app)}).")
        return 0
    print(
        f"{len(too_new)} bundled Mach-O(s) need a newer macOS than the declared "
        f"LSMinimumSystemVersion {format_version(minimum)} (openfollow.spec):",
        file=sys.stderr,
    )
    for path, needed in too_new:
        print(f"  {path.relative_to(app)}: macOS {format_version(needed)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

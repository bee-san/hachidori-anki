#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the installable Hachidori Relay archive and its SHA-256 checksum."""
import argparse
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE_NAME = "hachidori-relay.ankiaddon"
ADDON_FILES = ("__init__.py", "config.json", "config.md", "manifest.json", "server.py")


def build_archive(root, modified, expected_version=None):
    """Anki reads these files at the ZIP root, with a stable package identity."""
    manifest = json.loads((root / "addon" / "manifest.json").read_text(encoding="utf-8"))
    if expected_version is not None and manifest["human_version"] != expected_version:
        raise ValueError(f"Release version {expected_version} does not match manifest version {manifest['human_version']}")
    manifest["mod"] = modified
    files = {name: (root / "addon" / name).read_bytes() for name in ADDON_FILES}
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files["LICENSE"] = (root / "LICENSE").read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in sorted(files):
            # Stored entries and fixed metadata produce the same bytes across platforms.
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, files[name])
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="output directory (default: dist)")
    parser.add_argument("--version", help="require the manifest to match this release version, without the v prefix")
    args = parser.parse_args()
    modified = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT, text=True).strip())
    data = build_archive(ROOT, modified, args.version)
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / FILE_NAME
    archive.write_bytes(data)
    checksum = args.output / f"{FILE_NAME}.sha256"
    checksum.write_text(f"{hashlib.sha256(data).hexdigest()}  {FILE_NAME}\n", encoding="utf-8")
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()

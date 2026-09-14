# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import io
import json
import runpy
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
build_archive = runpy.run_path(str(ROOT / "scripts" / "package-addon.py"))["build_archive"]


class PackageTest(unittest.TestCase):
    def test_installable_archive_is_reproducible_and_contains_only_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "addon", root / "addon")
            shutil.copyfile(ROOT / "LICENSE", root / "LICENSE")
            (root / "addon" / "__pycache__").mkdir(exist_ok=True)
            (root / "addon" / "__pycache__" / "server.pyc").write_bytes(b"cached Python")
            (root / "addon" / "meta.json").write_text('{"config":{"port":9000}}')
            data = build_archive(root, 1_757_000_000)
            self.assertEqual(data, build_archive(root, 1_757_000_000))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(), ["LICENSE", "__init__.py", "config.json", "config.md", "manifest.json", "server.py"])
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest, {
                    **json.loads((root / "addon" / "manifest.json").read_text()),
                    "package": "hachidori-relay", "name": "Hachidori Relay", "mod": 1_757_000_000,
                })
                for entry in archive.infolist():
                    self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                    if entry.filename == "manifest.json":
                        continue
                    source = root / "LICENSE" if entry.filename == "LICENSE" else root / "addon" / entry.filename
                    self.assertEqual(archive.read(entry), source.read_bytes())
                    if entry.filename.endswith(".py"):
                        compile(archive.read(entry), entry.filename, "exec")

    def test_release_version_must_match_manifest(self):
        with self.assertRaisesRegex(ValueError, "does not match manifest version"):
            build_archive(ROOT, 1_757_000_000, "wrong-version")

    def test_command_writes_archive_and_matching_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            version = json.loads((ROOT / "addon" / "manifest.json").read_text())["human_version"]
            subprocess.run(["python3", str(ROOT / "scripts" / "package-addon.py"), "--output", temporary, "--version", version], check=True, capture_output=True)
            archive = Path(temporary) / "hachidori-relay.ankiaddon"
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(archive.with_suffix(".ankiaddon.sha256").read_text(), f"{digest}  {archive.name}\n")
            modified = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT, text=True))
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(json.loads(package.read("manifest.json"))["mod"], modified)


if __name__ == "__main__":
    unittest.main()

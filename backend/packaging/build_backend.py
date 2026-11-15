# -*- coding: utf-8 -*-
"""Build the portable backend bundle with PyInstaller.

Usage (from the repository root, with the backend virtualenv active)::

    python backend/packaging/build_backend.py

Output: ``backend/dist/paperex-server/`` (onedir).  The Tauri installer copies
that directory into the app resources as ``backend/`` and the launcher starts
``paperex-server.exe`` instead of a system Python.

The build also bakes the engine fingerprint into
``packaging/_build/engine_revision.txt`` so the frozen app reports the same
engine revision as the development tree (the fingerprint normally hashes
source files which do not exist inside a frozen bundle).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGING_DIR.parent
REPO_DIR = BACKEND_DIR.parent
DIST_DIR = BACKEND_DIR / "dist"
BUILD_DIR = PACKAGING_DIR / "_build"
SPEC_FILE = PACKAGING_DIR / "paperex-server.spec"
OUTPUT_DIR = DIST_DIR / "paperex-server"
APP_NAME = "paperex-server"


def read_app_version() -> str:
    text = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("cannot find APP_VERSION in backend/main.py")
    return match.group(1)


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts: list[int] = []
    for chunk in version.split(".")[:4]:
        digits = re.match(r"\d+", chunk)
        parts.append(int(digits.group(0)) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


VERSION_INFO_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'paperex contributors'),
        StringStruct('FileDescription', 'paperex local translation backend'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{name}'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 paperex contributors'),
        StringStruct('OriginalFilename', '{name}.exe'),
        StringStruct('ProductName', 'paperex'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def write_version_info(version: str) -> Path:
    target = BUILD_DIR / "version_info.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    numbers = version_tuple(version)
    target.write_text(
        VERSION_INFO_TEMPLATE.format(numbers=numbers, version=version, name=APP_NAME),
        encoding="utf-8",
    )
    return target


def bake_engine_revision() -> str:
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        import engine_adapter  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    revision = engine_adapter.engine_fingerprint(BACKEND_DIR)
    target = BUILD_DIR / "engine_revision.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(revision + "\n", encoding="utf-8")
    return revision


def pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="build the frozen paperex backend")
    parser.add_argument("--skip-bake-check", action="store_true", help="do not fail if PyInstaller is missing")
    parser.add_argument("--keep-work", action="store_true", help="keep PyInstaller work directory")
    args = parser.parse_args()

    if not pyinstaller_available():
        if args.skip_bake_check:
            print("PyInstaller is not installed; install it first: pip install pyinstaller", file=sys.stderr)
            return 2
        print("PyInstaller is not installed; install it first: pip install pyinstaller", file=sys.stderr)
        return 2

    version = read_app_version()
    print(f"version: {version}")
    revision = bake_engine_revision()
    print(f"engine revision: {revision}")
    write_version_info(version)

    work_dir = BUILD_DIR / "work"
    for path in (work_dir, OUTPUT_DIR):
        if path.exists():
            shutil.rmtree(path)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(work_dir),
        str(SPEC_FILE),
    ]
    print("running:", " ".join(command))
    started = time.monotonic()
    completed = subprocess.run(command, cwd=str(REPO_DIR))
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        print(f"PyInstaller failed with exit code {completed.returncode}", file=sys.stderr)
        return completed.returncode

    exe = OUTPUT_DIR / f"{APP_NAME}.exe"
    if not exe.is_file():
        print(f"expected executable missing: {exe}", file=sys.stderr)
        return 1

    if not args.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    size_mb = dir_size(OUTPUT_DIR) / (1024 * 1024)
    exe_mb = exe.stat().st_size / (1024 * 1024)
    info = {
        "version": version,
        "engine_revision": revision,
        "output": str(OUTPUT_DIR),
        "exe": str(exe),
        "bundle_mb": round(size_mb, 1),
        "exe_mb": round(exe_mb, 1),
        "elapsed_s": round(elapsed, 1),
    }
    (BUILD_DIR / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

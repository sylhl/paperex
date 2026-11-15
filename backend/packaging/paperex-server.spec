# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable paperex backend.

Build from the repository root with::

    python backend/packaging/build_backend.py

The output is ``backend/dist/paperex-server/`` (onedir) which the Tauri
installer bundles as the app's ``backend/`` resource directory.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
BACKEND_DIR = SPEC_DIR.parent
REPO_DIR = BACKEND_DIR.parent
BUILD_DIR = SPEC_DIR / "_build"

datas = [
    (str(BACKEND_DIR / "assets" / "fonts"), "assets/fonts"),
    (str(BACKEND_DIR / "pdf_zh_translator" / "corpus.json"), "pdf_zh_translator"),
    (str(BACKEND_DIR / "pdf_zh_translator" / "corpora"), "pdf_zh_translator/corpora"),
    (str(BACKEND_DIR / "pdf_zh_translator" / "layout_families.json"), "pdf_zh_translator"),
]

# engine_fingerprint() is baked at build time because the frozen bundle has
# no source files to hash (see engine_adapter.engine_fingerprint).
revision_file = BUILD_DIR / "engine_revision.txt"
if revision_file.is_file():
    datas.append((str(revision_file), "assets"))

hiddenimports = sorted(
    set(
        collect_submodules("uvicorn")
        + collect_submodules("multipart")
        + [
            "multipart",
            "uvicorn",
        ]
    )
)

icon_file = REPO_DIR / "frontend" / "src-tauri" / "icons" / "icon.ico"
version_file = BUILD_DIR / "version_info.txt"

# Optional PyMuPDF ML assets (layout/table ONNX models, C headers) are several
# tens of MB and are not used by paperex's own engine; drop them so the
# installer stays small. ``numpy``/``PIL`` are optional pymupdf accelerators
# (the engine falls back to pure Python if numpy is missing), so they are
# excluded below as well.
EXCLUDED_DATA_PREFIXES = (
    "pymupdf/layout",
    "pymupdf/mupdf-devel",
    "numpy",
    "numpy.libs",
)

a = Analysis(  # noqa: F821 - provided by PyInstaller
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(BACKEND_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytest_asyncio",
        "ruff",
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
    ],
    noarchive=False,
    optimize=0,
)


def _wanted(entry) -> bool:
    destination = str(entry[0]).replace("\\", "/")
    return not destination.startswith(EXCLUDED_DATA_PREFIXES)


a.datas = [entry for entry in a.datas if _wanted(entry)]
a.binaries = [entry for entry in a.binaries if _wanted(entry)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="paperex-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.is_file() else None,
    version=str(version_file) if version_file.is_file() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="paperex-server",
)

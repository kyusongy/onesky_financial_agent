# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OneSky Financial Agent desktop bundle."""

from PyInstaller.utils.hooks import collect_all, copy_metadata

# Collect Streamlit + its runtime assets (it reads files from its own package dir).
streamlit_datas, streamlit_binaries, streamlit_hiddenimports = collect_all("streamlit")
pandas_datas, pandas_binaries, pandas_hiddenimports = collect_all("pandas")
openpyxl_datas, openpyxl_binaries, openpyxl_hiddenimports = collect_all("openpyxl")

# Streamlit checks package metadata at runtime.
metadata = (
    copy_metadata("streamlit")
    + copy_metadata("pandas")
    + copy_metadata("openpyxl")
    + copy_metadata("numpy")
)

# Project data files bundled into the .app:
# - app.py            : the Streamlit entry the embedded server runs
# - config/, src/     : importable Python packages referenced by the app
# - instruction_data/templates/defaults/ : default lookup + output template files
project_datas = [
    ("app.py", "."),
    ("config", "config"),
    ("src", "src"),
    ("instruction_data/templates/defaults", "instruction_data/templates/defaults"),
    ("instruction_data/templates/output_template.xlsx", "instruction_data/templates"),
]

a = Analysis(
    ["run_desktop.py"],
    pathex=["."],
    binaries=streamlit_binaries + pandas_binaries + openpyxl_binaries,
    datas=(
        streamlit_datas
        + pandas_datas
        + openpyxl_datas
        + metadata
        + project_datas
    ),
    hiddenimports=(
        streamlit_hiddenimports
        + pandas_hiddenimports
        + openpyxl_hiddenimports
        + ["src", "src.lookup_parsers", "config", "config.mappings"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OneSky Financial Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OneSky Financial Agent",
)

app = BUNDLE(
    coll,
    name="OneSky Financial Agent.app",
    icon="scripts/icon.icns",
    bundle_identifier="com.onesky.financial-agent",
    version="0.1.0",
    info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "© 2026 OneSky",
    },
)

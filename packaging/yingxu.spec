# -*- mode: python ; coding: utf-8 -*-
# Build from the repository root:
#   pyinstaller packaging/yingxu.spec
# This produces a windowed Windows/Linux folder build. It is not an installer.

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
hidden = collect_submodules("yingxu")

a = Analysis(
    ["../yingxu/__main__.py"],
    pathex=[".."],
    binaries=[],
    datas=[("../presets", "presets")],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yingxu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="yingxu",
)

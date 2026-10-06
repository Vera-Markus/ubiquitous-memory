# -*- mode: python ; coding: utf-8 -*-
import os
import re

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

# Windows version information (Properties > Details). A program without it looks more
# suspicious to antivirus heuristics; RC2 was flagged Trojan:Win32/Wacatac.B!ml.
with open(os.path.join(SPECPATH, "app", "version.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
_numbers = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:3]) + (0,)
_strings = {
    "CompanyName": "EVE Fleet Management Tool",
    "FileDescription": "EVE Fleet Management Tool",
    "FileVersion": VERSION,
    "InternalName": "EveFleetManagementTool",
    "LegalCopyright": "MIT License. EVE Online and all related marks are property of CCP hf.",
    "OriginalFilename": "EveFleetManagementTool.exe",
    "ProductName": "EVE Fleet Management Tool",
    "ProductVersion": VERSION,
}
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers, prodvers=_numbers),
    kids=[
        StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in _strings.items()])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ['run_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('Assets', 'Assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EveFleetManagementTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,      # UPX-packed programs trip antivirus heuristics
    console=False,
    version=version_info,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
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
    name='EveFleetManagementTool',
)


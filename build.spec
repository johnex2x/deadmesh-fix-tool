# PyInstaller spec for DeadMesh Fix Tool.
# Build: .venv\Scripts\python -m PyInstaller build.spec
# Output: dist/DeadMeshFixTool/DeadMeshFixTool.exe (onedir: faster start, easier AV scanning)
import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT / "src"))
from dmfix.version import __version__

version_parts = tuple(int(part) for part in __version__.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts, prodvers=version_parts),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "johnex2x"),
                StringStruct("FileDescription", "DeadMesh Fix Tool"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "DeadMeshFixTool"),
                StringStruct("OriginalFilename", "DeadMeshFixTool.exe"),
                StringStruct("ProductName", "DeadMesh Fix Tool"),
                StringStruct("ProductVersion", __version__),
            ])
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / "src" / "dmfix" / "main.py")],
    pathex=[str(ROOT / "src"), str(ROOT / "vendor")],
    binaries=[
        # NiflyDLL is loaded via ctypes from the vendor dir next to pyn/.
        (str(ROOT / "vendor" / "NiflyDLL.dll"), "vendor"),
    ],
    datas=[
        (str(ROOT / "vendor" / "pyn"), "vendor/pyn"),
        (str(ROOT / "vendor" / "mopp_verifier.py"), "vendor"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "README.md"), "."),
        (str(ROOT / "assets" / "icon.ico"), "assets"),
    ],
    hiddenimports=[
        "dmfix.core.fixes.degenerate",
        "dmfix.core.fixes.winding",
        "dmfix.core.fixes.orphan",
        "lz4.frame",
    ],
    excludes=["tkinter", "matplotlib", "scipy", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Two launchers over the same bundle: the windowed one for double-click GUI
# use (no console window), the console one for CLI/scripting output.
exe_gui = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="DeadMeshFixTool",
    console=False,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=version_info,
)

exe_cli = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="dmfix",
    console=True,
    icon=None,
    version=version_info,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    name="DeadMeshFixTool",
)

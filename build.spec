# PyInstaller spec for DeadMesh Fix Tool.
# Build: .venv\Scripts\python -m PyInstaller build.spec
# Output: dist/DeadMeshFixTool/DeadMeshFixTool.exe (onedir: faster start, easier AV scanning)
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

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

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="DeadMeshFixTool",
    console=True,  # CLI mode needs a console; GUI hides it via windowed launcher note in README
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="DeadMeshFixTool",
)

"""Resource path resolution that works both from source and PyInstaller-frozen."""
from __future__ import annotations

import sys
from pathlib import Path


def vendor_dir() -> Path:
    """Folder containing the vendored pyn/ package, NiflyDLL.dll and mopp_verifier."""
    if getattr(sys, "frozen", False):
        # PyInstaller: bundled data lands under _MEIPASS (_internal for onedir).
        return Path(getattr(sys, "_MEIPASS")) / "vendor"
    return Path(__file__).resolve().parents[3] / "vendor"


def ensure_vendor_on_path() -> Path:
    vendor = vendor_dir()
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    return vendor


def icon_path() -> Path:
    """Path to the app icon (assets/icon.ico), both from source and frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "assets" / "icon.ico"
    return Path(__file__).resolve().parents[3] / "assets" / "icon.ico"

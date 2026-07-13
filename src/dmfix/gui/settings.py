"""Persistent user preferences for the GUI."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_CATEGORIES = [
    "crash",
    "heavy",
    "degenerate",
    "inverted",
    "orphan_blocks",
]


@dataclass
class Settings:
    deadmesh_dir: str = ""
    language: str = "en"
    last_target_folder: str = ""
    strength: str = "normal"
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    include_bsa: bool = True


def _settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "DeadMesh Fix Tool" / "settings.json"


def load() -> Settings:
    """Load settings, returning safe defaults when the file is unusable."""
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Settings()
        string_fields = ("deadmesh_dir", "language", "last_target_folder", "strength")
        if any(name in data and not isinstance(data[name], str) for name in string_fields):
            return Settings()
        if "language" in data and data["language"] not in ("en", "zh-TW"):
            return Settings()
        if "strength" in data and data["strength"] not in (
            "conservative",
            "normal",
            "aggressive",
        ):
            return Settings()
        if "include_bsa" in data and not isinstance(data["include_bsa"], bool):
            return Settings()
        if "categories" in data and (
            not isinstance(data["categories"], list)
            or any(category not in DEFAULT_CATEGORIES for category in data["categories"])
        ):
            return Settings()
        values = {
            name: data[name]
            for name in Settings.__dataclass_fields__
            if name in data
        }
        return Settings(**values)
    except (OSError, ValueError, TypeError):
        return Settings()


def save(settings: Settings) -> None:
    """Persist settings as UTF-8 JSON in the user's roaming profile."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

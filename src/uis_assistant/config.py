"""Settings from data/config.toml, overridable by environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_TEMPLATE = """\
# UiS assistant settings. Environment variables override these (UIS_*, CANVAS_TOKEN).

base_url = "https://stavanger.instructure.com"
timezone = "Europe/Oslo"

# Semester window. Leave empty to derive it from the Canvas course terms.
# window_start = "2026-08-01"
# window_end = "2026-12-31"

# Remind about unsubmitted deadlines this many hours before they're due
reminder_hours = [48, 6]

# Hold notifications between these local hours (start inclusive, end exclusive)
quiet_start = 23
quiet_end = 7

# Alert after this many failed syncs in a row (token rejection alerts immediately)
failure_alert_after = 3
"""


class ConfigError(Exception):
    pass


def _default_data_dir() -> Path:
    if env := os.environ.get("UIS_DATA_DIR"):
        return Path(env).expanduser()
    # src/uis_assistant/config.py -> project root, when running from a checkout
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists():
        return root / "data"
    return Path.home() / ".local" / "share" / "uis-assistant"


@dataclass
class Config:
    data_dir: Path
    base_url: str = "https://stavanger.instructure.com"
    timezone: str = "Europe/Oslo"
    window_start: date | None = None
    window_end: date | None = None
    reminder_hours: list[int] = field(default_factory=lambda: [48, 6])
    quiet_start: int = 23
    quiet_end: int = 7
    failure_alert_after: int = 3

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "uis.db"

    @property
    def token_file(self) -> Path:
        return self.data_dir / "canvas_token"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.toml"

    def token(self) -> str:
        if tok := os.environ.get("CANVAS_TOKEN"):
            return tok.strip()
        try:
            tok = self.token_file.read_text().strip()
        except FileNotFoundError:
            raise ConfigError(f"No Canvas token: set CANVAS_TOKEN or run `uis init` to create {self.token_file}") from None
        if not tok:
            raise ConfigError(f"Canvas token file is empty: {self.token_file}")
        return tok


def load_config(data_dir: Path | None = None) -> Config:
    data_dir = data_dir or _default_data_dir()
    cfg = Config(data_dir=data_dir)
    path = data_dir / "config.toml"
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)
        for key in ("base_url", "timezone", "reminder_hours", "quiet_start", "quiet_end", "failure_alert_after"):
            if key in raw:
                setattr(cfg, key, raw[key])
        for key in ("window_start", "window_end"):
            if raw.get(key):
                v = raw[key]
                setattr(cfg, key, v if isinstance(v, date) else date.fromisoformat(v))
    if url := os.environ.get("UIS_BASE_URL"):
        cfg.base_url = url
    cfg.base_url = cfg.base_url.rstrip("/")
    cfg.reminder_hours = sorted({int(h) for h in cfg.reminder_hours}, reverse=True)
    return cfg

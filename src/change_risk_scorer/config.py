"""Load and validate the YAML/JSON configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_THRESHOLDS = {"medium": 25, "high": 50, "critical": 75}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PathRule:
    name: str
    patterns: tuple[str, ...]
    points: int
    reason: str = ""


@dataclass(frozen=True)
class FreezeWindow:
    """Either a dated window (start/end) or a weekly one (days + start_time/end_time)."""
    name: str
    points: int = 40
    start: datetime | None = None
    end: datetime | None = None
    days: tuple[int, ...] = ()
    start_time: time = time(0, 0)
    end_time: time = time(23, 59, 59)

    def active(self, now: datetime) -> bool:
        if self.start is not None and self.end is not None:
            return self.start <= now <= self.end
        return now.weekday() in self.days and self.start_time <= now.time() <= self.end_time


@dataclass(frozen=True)
class BusinessHours:
    start: time
    end: time
    days: tuple[int, ...]
    points: int = 10


@dataclass
class Config:
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    path_rules: list[PathRule] = field(default_factory=list)
    service_roots: tuple[str, ...] = ()
    freeze_windows: list[FreezeWindow] = field(default_factory=list)
    business_hours: BusinessHours | None = None
    thresholds: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    @property
    def has_timing(self) -> bool:
        return bool(self.freeze_windows or self.business_hours)


def _time(value: Any, what: str) -> time:
    if isinstance(value, int):  # YAML reads an unquoted 15:00 as the base-60 integer 900
        return time(value // 60, value % 60)
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{what}: expected a time like \"17:00\", got {value!r}") from exc


def _datetime(value: Any, tz: ZoneInfo, what: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ConfigError(f"{what}: expected an ISO date/time, got {value!r}") from exc
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _days(value: Any, what: str) -> tuple[int, ...]:
    out = []
    for d in value or []:
        key = str(d).lower()[:3]
        if key not in DAYS:
            raise ConfigError(f"{what}: unknown day {d!r} (use mon, tue, ... sun)")
        out.append(DAYS.index(key))
    return tuple(out)


def _mapping(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{what}: expected a mapping")
    return value


def parse_config(data: Any) -> Config:
    if data is None:
        return Config()
    data = _mapping(data, "config")
    known = {"timezone", "paths", "services", "freeze_windows", "business_hours", "thresholds"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")

    try:
        tz = ZoneInfo(str(data.get("timezone", "UTC")))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"timezone: unknown zone {data.get('timezone')!r}") from exc
    cfg = Config(timezone=tz)

    for i, raw in enumerate(data.get("paths") or []):
        raw = _mapping(raw, f"paths[{i}]")
        if not raw.get("name") or not raw.get("patterns"):
            raise ConfigError(f"paths[{i}]: needs name and patterns")
        cfg.path_rules.append(PathRule(str(raw["name"]), tuple(map(str, raw["patterns"])),
                                       int(raw.get("points", 15)), str(raw.get("reason", ""))))

    services = data.get("services") or {}
    cfg.service_roots = tuple(str(r).strip("/") for r in _mapping(services, "services").get("roots") or [])

    for i, raw in enumerate(data.get("freeze_windows") or []):
        what = f"freeze_windows[{i}]"
        raw = _mapping(raw, what)
        name, points = str(raw.get("name", f"window {i + 1}")), int(raw.get("points", 40))
        if "start" in raw and "end" in raw:
            start, end = _datetime(raw["start"], tz, what), _datetime(raw["end"], tz, what)
            if end < start:
                raise ConfigError(f"{what}: end is before start")
            cfg.freeze_windows.append(FreezeWindow(name, points, start=start, end=end))
        elif raw.get("days"):
            cfg.freeze_windows.append(FreezeWindow(
                name, points, days=_days(raw["days"], what),
                start_time=_time(raw.get("start_time", "00:00"), what),
                end_time=_time(raw.get("end_time", "23:59:59"), what)))
        else:
            raise ConfigError(f"{what}: needs either start and end, or days")

    if data.get("business_hours"):
        bh = _mapping(data["business_hours"], "business_hours")
        cfg.business_hours = BusinessHours(
            _time(bh.get("start", "09:00"), "business_hours.start"),
            _time(bh.get("end", "17:00"), "business_hours.end"),
            _days(bh.get("days", DAYS[:5]), "business_hours.days"),
            int(bh.get("points", 10)))

    cfg.thresholds.update({k: int(v) for k, v in _mapping(data.get("thresholds") or {}, "thresholds").items()})
    if set(cfg.thresholds) != set(DEFAULT_THRESHOLDS):
        raise ConfigError("thresholds: only medium, high and critical can be set")
    if not cfg.thresholds["medium"] <= cfg.thresholds["high"] <= cfg.thresholds["critical"]:
        raise ConfigError("thresholds: must satisfy medium <= high <= critical")
    return cfg


def load_config(path: str | None) -> Config:
    """Load a .json, .yml or .yaml file. `None` gives the built-in defaults."""
    if path is None:
        return Config()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc.strerror}") from exc
    try:
        data = json.loads(text) if path.endswith(".json") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot parse config {path}: {exc}") from exc
    return parse_config(data)

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import ensure_dirs, runtime_dir
from .profiles import validate_name

STATE_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(value), timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ttl_seconds(expires_at: Any, *, now: datetime | None = None) -> int | None:
    expiry = parse_time(expires_at)
    if expiry is None:
        return None
    current = now or utc_now()
    return int((expiry - current).total_seconds())


def state_path(profile_name: str) -> Path:
    return runtime_dir() / f"{validate_name(profile_name)}.session.json"


def default_state(profile_name: str) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "profile": validate_name(profile_name),
        "auth_state": "UNKNOWN",
        "browser_state": "UNKNOWN",
        "needs_login": False,
        "last_checked_at": None,
        "last_health_check_at": None,
        "last_auth_check_at": None,
        "last_auth_success_at": None,
        "last_auth_refresh_at": None,
        "last_upstream_auth_recover_at": None,
        "last_upstream_auth_recover_rc": None,
        "token_expires_at": None,
        "token_ttl_seconds": None,
        "session_reported_expires_at": None,
        "last_successful_send_at": None,
        "last_browser_recovery_at": None,
        "last_recovery_reason": None,
        "consecutive_auth_failures": 0,
        "consecutive_health_failures": 0,
        "consecutive_broken_checks": 0,
        "last_error": None,
    }


def load_state(profile_name: str) -> dict[str, Any]:
    path = state_path(profile_name)
    if not path.exists():
        return default_state(profile_name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state(profile_name)
    state = default_state(profile_name)
    if isinstance(raw, dict):
        state.update(raw)
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["profile"] = validate_name(profile_name)
    return state


def save_state(profile_name: str, state: dict[str, Any]) -> Path:
    ensure_dirs()
    path = state_path(profile_name)
    payload = deepcopy(state)
    payload["schema_version"] = STATE_SCHEMA_VERSION
    payload["profile"] = validate_name(profile_name)
    tmp = path.with_suffix(".session.json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


def update_state(profile_name: str, **changes: Any) -> dict[str, Any]:
    state = load_state(profile_name)
    state.update(changes)
    state["last_checked_at"] = iso_now()
    save_state(profile_name, state)
    return state


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    sign = "-" if seconds < 0 else ""
    n = abs(int(seconds))
    days, rem = divmod(n, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return sign + " ".join(parts)

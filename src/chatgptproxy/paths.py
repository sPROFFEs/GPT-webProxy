from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    override = os.environ.get("CHATGPTPROXY_HOME")
    return Path(override).expanduser() if override else Path.home() / ".chatgptproxy"


def profiles_dir() -> Path:
    return home() / "profiles"


def runtime_dir() -> Path:
    return home() / "runtime"


def logs_dir() -> Path:
    return home() / "logs"


def browser_dir(profile: str) -> Path:
    return home() / "browser" / profile


def ensure_dirs() -> None:
    for path in (profiles_dir(), runtime_dir(), logs_dir(), home() / "browser"):
        path.mkdir(parents=True, exist_ok=True)

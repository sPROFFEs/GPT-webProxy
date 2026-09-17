from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .paths import browser_dir, ensure_dirs, profiles_dir, runtime_dir

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _new_key() -> str:
    return "cgp_" + secrets.token_urlsafe(32)


def default_profile(name: str = "default") -> dict[str, Any]:
    return {
        "name": name,
        # Public chatgptproxy gateway.
        "host": "127.0.0.1",
        "port": 1235,
        # ChatGPT-Web2API is intentionally kept behind the gateway.
        "upstream_host": "127.0.0.1",
        "upstream_port": 13335,
        "cdp_port": 9232,
        "mcp_port": 1236,
        "default_model": "auto",
        "headless": False,
        "tab_mode": "owned",
        "parallel_tabs": False,
        "request_timeout": 120,
        "api_key": _new_key(),
        "mcp_enabled": False,
        "mcp_write": False,
        "mcp_destructive": False,
        "diagnose": False,
        # Product mode / local workspace layer.
        "workspace_mode": "chat_only",
        "workspace_path": None,
        "context_policy": "none",
        "workspace_write_enabled": False,
        "exec_enabled": False,
        "tool_mode": "guarded",
        "workspace_max_steps": 6,
        "workspace_max_actions_per_step": 8,
        "exec_timeout_seconds": 30,
        "workspace_max_file_bytes": 262144,
        "workspace_max_write_bytes": 1048576,
        "workspace_max_output_bytes": 65536,
        "workspace_max_tool_feedback_bytes": 131072,
        "workspace_search_max_files": 1000,
        "workspace_include_hidden": False,
        "exec_allowlist": [
            "bash", "sh", "git", "node", "npm", "npx", "pnpm", "yarn", "bun",
            "python", "python3", "pytest", "uv", "pip", "pip3",
            "go", "cargo", "rustc", "make", "cmake", "ninja",
            "gcc", "g++", "clang", "clang++", "java", "javac", "mvn", "gradle",
            "dotnet", "php", "ruby", "bundle", "composer",
            "ruff", "black", "mypy", "eslint", "prettier", "tsc",
        ],
        # Browser/session lifecycle.
        "session_supervisor_enabled": True,
        "health_check_interval_seconds": 30,
        "auth_check_interval_seconds": 300,
        "auth_refresh_lead_seconds": 600,
        "auth_probe_timeout": 8,
        "auth_recover_retry_seconds": 60,
        "auto_recover_browser": True,
        "health_failure_restart_threshold": 3,
        "broken_restart_threshold": 2,
        "browser_restart_cooldown_seconds": 120,
    }


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ValueError("profile name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return name


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{validate_name(name)}.json"


def runtime_config_path(name: str) -> Path:
    return runtime_dir() / f"{validate_name(name)}.upstream.json"


def save_profile(profile: dict[str, Any], *, overwrite: bool = True) -> Path:
    ensure_dirs()
    name = validate_name(str(profile["name"]))
    path = profile_path(name)
    if path.exists() and not overwrite:
        raise FileExistsError(f"profile already exists: {name}")
    payload = deepcopy(profile)
    payload["name"] = name
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def _merge_defaults(data: dict[str, Any], name: str) -> dict[str, Any]:
    base = default_profile(name)
    base.update(data)
    base["name"] = name
    # v0.2 profiles exposed ChatGPT-Web2API directly on `port`; v0.3 inserts
    # a gateway and assigns a separate internal upstream port.
    if "upstream_port" not in data:
        public_port = int(base.get("port", 1235))
        base["upstream_port"] = public_port + 12100
    return base


def load_profile(name: str = "default") -> dict[str, Any]:
    path = profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid profile file: {path}")
    return _merge_defaults(data, validate_name(name))


def ensure_default_profile() -> dict[str, Any]:
    ensure_dirs()
    path = profile_path("default")
    if not path.exists():
        save_profile(default_profile())
    return load_profile("default")


def list_profiles() -> list[str]:
    ensure_dirs()
    return sorted(p.stem for p in profiles_dir().glob("*.json"))


def delete_profile(name: str) -> None:
    profile_path(name).unlink()
    runtime_config_path(name).unlink(missing_ok=True)
    (runtime_dir() / f"{validate_name(name)}.session.json").unlink(missing_ok=True)


def clone_profile(source: str, target: str) -> dict[str, Any]:
    src = load_profile(source)
    validate_name(target)
    clone = deepcopy(src)
    clone["name"] = target
    clone["api_key"] = _new_key()
    save_profile(clone, overwrite=False)
    return clone


def find_chrome_binary(explicit: str | None = None) -> str | None:
    if explicit and explicit != "auto":
        resolved = shutil.which(explicit) or explicit
        if Path(resolved).exists():
            return str(Path(resolved).resolve())
        return explicit
    # Search common system names and paths
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(path).exists() and os.access(path, os.X_OK):
            return path
    # Check local m365proxy / playwright browser installations if present
    m365_browsers = Path.home() / ".local" / "share" / "m365proxy" / "browsers"
    if m365_browsers.exists():
        for candidate in sorted(m365_browsers.glob("**/chrome"), reverse=True):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    playwright_dir = Path.home() / ".cache" / "ms-playwright"
    if playwright_dir.exists():
        for candidate in sorted(playwright_dir.glob("**/chrome"), reverse=True):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    return None


def upstream_config(profile: dict[str, Any]) -> dict[str, Any]:
    name = validate_name(str(profile["name"]))
    cfg: dict[str, Any] = {
        "port": int(profile.get("upstream_port", 13335)),
        "host": str(profile.get("upstream_host", "127.0.0.1")),
        "cdp_port": int(profile["cdp_port"]),
        "user_data_dir": str(browser_dir(name)),
        "headless": bool(profile.get("headless", False)),
        "default_model": str(profile.get("default_model", "auto")),
        "default_project_id": profile.get("default_project_id"),
        "tab_mode": str(profile.get("tab_mode", "owned")),
        "parallel_tabs": bool(profile.get("parallel_tabs", False)),
        "api_keys": [str(profile["api_key"])],
        "request_timeout": int(profile.get("request_timeout", 120)),
        "log_level": str(profile.get("log_level", "INFO")),
    }
    chrome_bin = find_chrome_binary(profile.get("chrome_path"))
    if chrome_bin:
        cfg["chrome_path"] = chrome_bin
    return cfg


def write_runtime_config(profile: dict[str, Any]) -> Path:
    ensure_dirs()
    path = runtime_config_path(str(profile["name"]))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(upstream_config(profile), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path

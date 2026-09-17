from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import cdp
from .session_state import iso_now, load_state, parse_time, save_state, ttl_seconds
from .upstream import health, status_snapshot


def _breakers(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    value = body.get("open_breakers")
    return [str(x) for x in value] if isinstance(value, list) else []


def _auth_state(authenticated: bool, ttl: int | None, refresh_lead: int) -> str:
    if not authenticated:
        return "NEEDS_LOGIN"
    if ttl is not None and ttl <= 0:
        return "EXPIRED"
    if ttl is not None and ttl <= refresh_lead:
        return "REFRESH_SOON"
    return "READY"


def check(profile: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    """Validate browser, upstream health and ChatGPT auth without sending a chat message.

    `refresh=True` records this probe as an intentional refresh. ChatGPT-Web2API's
    documented auth flow uses GET /api/auth/session to obtain a fresh access token,
    so the browser-side probe is the refresh mechanism as well as validation.
    The token itself is never persisted by chatgptproxy.
    """
    name = str(profile["name"])
    state = load_state(name)
    now = datetime.now(timezone.utc)
    state["last_checked_at"] = iso_now()
    state["last_health_check_at"] = state["last_checked_at"]

    snapshot = status_snapshot(profile)
    health_body = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else None
    state["process_alive"] = bool(snapshot.get("process_alive"))
    state["upstream_status"] = health_body.get("status") if health_body else None
    state["last_successful_send_at"] = health_body.get("last_successful_send_at") if health_body else state.get("last_successful_send_at")

    breakers = _breakers(health_body)
    state["open_breakers"] = breakers
    chrome_ok = bool(health_body and health_body.get("chrome_running") is True)
    driver_ok = bool(health_body and health_body.get("driver_connected") is True)
    state["browser_state"] = "READY" if chrome_ok and driver_ok else ("DEGRADED" if health_body is not None else "DOWN")

    if snapshot.get("http_status") == 200:
        state["consecutive_health_failures"] = 0
    else:
        state["consecutive_health_failures"] = int(state.get("consecutive_health_failures") or 0) + 1

    if health_body and health_body.get("status") == "broken":
        state["consecutive_broken_checks"] = int(state.get("consecutive_broken_checks") or 0) + 1
    else:
        state["consecutive_broken_checks"] = 0

    state["last_auth_check_at"] = iso_now()
    try:
        auth = cdp.auth_session(int(profile["cdp_port"]), timeout=float(profile.get("auth_probe_timeout", 8)))
    except cdp.CDPError as exc:
        state["consecutive_auth_failures"] = int(state.get("consecutive_auth_failures") or 0) + 1
        state["last_error"] = str(exc)
        if "auth_required" in breakers:
            state["auth_state"] = "NEEDS_LOGIN"
            state["needs_login"] = True
        elif state["browser_state"] == "DOWN":
            state["auth_state"] = "UNKNOWN"
        else:
            state["auth_state"] = "CHECK_FAILED"
        save_state(name, state)
        return state

    expiry = auth.get("token_expires_at")
    ttl = ttl_seconds(expiry, now=now)
    authenticated = bool(auth.get("authenticated"))
    refresh_lead = int(profile.get("auth_refresh_lead_seconds", 600))
    state["token_expires_at"] = expiry
    state["token_ttl_seconds"] = ttl
    state["session_reported_expires_at"] = auth.get("reported_expires_at")
    state["auth_http_status"] = auth.get("http_status")
    state["auth_state"] = _auth_state(authenticated, ttl, refresh_lead)
    state["needs_login"] = not authenticated
    state["last_error"] = auth.get("error") if not authenticated else None

    if authenticated:
        state["consecutive_auth_failures"] = 0
        state["last_auth_success_at"] = iso_now()
        if refresh:
            state["last_auth_refresh_at"] = state["last_auth_success_at"]
    else:
        state["consecutive_auth_failures"] = int(state.get("consecutive_auth_failures") or 0) + 1

    # The upstream breaker is authoritative for a failed login. A successful
    # browser session probe means the session itself is valid even if the
    # upstream breaker has not yet been reset by its next preflight.
    if "auth_required" in breakers and not authenticated:
        state["auth_state"] = "NEEDS_LOGIN"
        state["needs_login"] = True

    save_state(name, state)
    return state


def should_auth_check(profile: dict[str, Any], state: dict[str, Any]) -> tuple[bool, bool]:
    now = datetime.now(timezone.utc)
    last = parse_time(state.get("last_auth_check_at"))
    interval = int(profile.get("auth_check_interval_seconds", 300))
    due = last is None or (now - last).total_seconds() >= interval

    ttl = ttl_seconds(state.get("token_expires_at"), now=now)
    refresh_lead = int(profile.get("auth_refresh_lead_seconds", 600))
    refresh = ttl is not None and ttl <= refresh_lead
    if state.get("auth_state") in {"NEEDS_LOGIN", "CHECK_FAILED", "UNKNOWN"}:
        due = True
    if "auth_required" in (state.get("open_breakers") or []):
        due = True
        refresh = True
    return due or refresh, refresh

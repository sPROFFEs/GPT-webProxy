from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

from .profiles import load_profile
from .session import check, should_auth_check
from .session_state import iso_now, load_state, parse_time, save_state
from .upstream import recover_managed_stack, run_ensure, status_snapshot

_STOP = False


def _signal_handler(_signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def _seconds_since(value: Any) -> float | None:
    dt = parse_time(value)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def tick(profile: dict[str, Any]) -> dict[str, Any]:
    name = str(profile["name"])
    state = load_state(name)
    snapshot = status_snapshot(profile)
    health_body = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    breakers = health_body.get("open_breakers") if isinstance(health_body.get("open_breakers"), list) else []
    auth_required = "auth_required" in breakers

    state["last_checked_at"] = iso_now()
    state["last_health_check_at"] = state["last_checked_at"]
    state["process_alive"] = bool(snapshot.get("process_alive"))
    state["upstream_status"] = health_body.get("status") if health_body else None
    state["open_breakers"] = breakers
    state["last_successful_send_at"] = health_body.get("last_successful_send_at", state.get("last_successful_send_at"))

    if snapshot.get("http_status") == 200:
        state["consecutive_health_failures"] = 0
    else:
        state["consecutive_health_failures"] = int(state.get("consecutive_health_failures") or 0) + 1

    if health_body.get("status") == "broken":
        state["consecutive_broken_checks"] = int(state.get("consecutive_broken_checks") or 0) + 1
    else:
        state["consecutive_broken_checks"] = 0

    if auth_required:
        state["auth_state"] = "NEEDS_LOGIN"
        state["needs_login"] = True

    save_state(name, state)

    # Auth checks are browser-side GETs only; no fake conversations are created.
    due, refresh = should_auth_check(profile, state)
    if due and (snapshot.get("process_alive") or snapshot.get("http_status") == 200):
        state = check(profile, refresh=refresh)

        # A valid browser session can recover a stale/expired access token, but
        # ChatGPT-Web2API owns the auth_required breaker. Its documented `ensure`
        # path invokes the upstream recovery logic and resets that breaker after
        # login/session recovery. Do this only after our browser probe succeeded.
        if auth_required and state.get("auth_state") in {"READY", "REFRESH_SOON"}:
            retry_after = int(profile.get("auth_recover_retry_seconds", 60))
            since_attempt = _seconds_since(state.get("last_upstream_auth_recover_at"))
            if since_attempt is None or since_attempt >= retry_after:
                state["last_upstream_auth_recover_at"] = iso_now()
                save_state(name, state)
                rc = run_ensure(profile)
                state = load_state(name)
                state["last_upstream_auth_recover_at"] = iso_now()
                state["last_upstream_auth_recover_rc"] = rc
                save_state(name, state)
                if rc == 0:
                    state = check(profile, refresh=False)
                    auth_required = False

    # Recovery policy mirrors upstream's guidance: never restart for auth_required,
    # never restart merely because status=degraded, and use a cooldown to prevent
    # browser flapping. Process death or persistent broken state can be recovered.
    auto_recover = bool(profile.get("auto_recover_browser", True))
    if not auto_recover or state.get("needs_login") or auth_required:
        return state

    restart_cooldown = int(profile.get("browser_restart_cooldown_seconds", 120))
    since_recovery = _seconds_since(state.get("last_browser_recovery_at"))
    cooldown_ok = since_recovery is None or since_recovery >= restart_cooldown
    health_fail_limit = int(profile.get("health_failure_restart_threshold", 3))
    broken_limit = int(profile.get("broken_restart_threshold", 2))

    must_recover = (
        (not snapshot.get("process_alive") and snapshot.get("http_status") is None)
        or int(state.get("consecutive_health_failures") or 0) >= health_fail_limit
        or int(state.get("consecutive_broken_checks") or 0) >= broken_limit
    )
    if must_recover and cooldown_ok:
        reason = "process_exit" if not snapshot.get("process_alive") else "persistent_unhealthy"
        recover_managed_stack(profile, reason=reason)
        state = load_state(name)
        state["last_browser_recovery_at"] = iso_now()
        state["last_recovery_reason"] = reason
        state["consecutive_health_failures"] = 0
        state["consecutive_broken_checks"] = 0
        save_state(name, state)
    return state


def run_loop(profile_name: str, *, once: bool = False) -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass

    while not _STOP:
        profile = load_profile(profile_name)
        try:
            tick(profile)
        except Exception as exc:
            state = load_state(profile_name)
            state["last_checked_at"] = iso_now()
            state["last_error"] = f"supervisor: {exc}"
            save_state(profile_name, state)
            if once:
                raise
        if once:
            return 0
        interval = max(10, int(profile.get("health_check_interval_seconds", 30)))
        for _ in range(interval):
            if _STOP:
                break
            time.sleep(1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m chatgptproxy.supervisor")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        return run_loop(args.profile, once=args.once)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"supervisor error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

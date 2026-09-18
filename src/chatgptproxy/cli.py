from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, cdp
from .paths import browser_dir, logs_dir
from .profiles import (
    clone_profile,
    default_profile,
    delete_profile,
    ensure_default_profile,
    find_chrome_binary,
    list_profiles,
    load_profile,
    save_profile,
)
from .session import check as session_check
from .session_state import format_duration, iso_now, load_state, save_state
from .supervisor import run_loop
from .upstream import (
    gateway_pid_path,
    mcp_pid_path,
    models,
    pid_path,
    print_json,
    process_alive,
    read_pid,
    recover_managed_stack,
    run_ensure,
    run_foreground,
    start_background,
    status_snapshot,
    stop_background,
    supervisor_pid_path,
    upstream_binary,
    upstream_mcp_binary,
)
from .workspace import apply_mode, mode_label, normalize_mode, validate_workspace_profile, WorkspaceExecutor


def _profile(name: str | None) -> dict[str, Any]:
    if name is None or name == "default":
        ensure_default_profile()
        name = "default"
    return load_profile(name)


def _ask(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _yes(value: str) -> bool:
    return value.lower() in {"y", "yes", "1", "true", "on"}


def _next_free_profile_port(field: str, base: int) -> int:
    used: set[int] = set()
    for name in list_profiles():
        try:
            used.add(int(load_profile(name).get(field, base)))
        except Exception:
            continue
    candidate = base
    while candidate in used:
        candidate += 1
    return candidate


def _choose_mode(default_mode: str = "full_workspace") -> str:
    default_num = {"full_workspace": "1", "read_only": "2", "chat_only": "3"}.get(normalize_mode(default_mode), "1")
    print("\nRecommended operating modes")
    print("  1. FULL WORKSPACE       [recommended for coding/project work]")
    print("     Read/write workspace files and run bounded development commands.")
    print("  2. WORKSPACE READ-ONLY  [recommended for reviews/audits]")
    print("     Inspect real project files; no writes and no local command execution.")
    print("  3. CHAT ONLY            [recommended for general ChatGPT use]")
    print("     No local filesystem or execution capability; preserves raw upstream streaming.")
    choice = _ask("Mode", default_num)
    mapping = {"1": "full_workspace", "2": "read_only", "3": "chat_only"}
    if choice in mapping:
        return mapping[choice]
    return normalize_mode(choice)


def _configure_mode_interactive(profile: dict[str, Any]) -> dict[str, Any]:
    mode = _choose_mode(str(profile.get("workspace_mode", "full_workspace")))
    workspace: str | None = None
    if mode != "chat_only":
        existing = str(profile.get("workspace_path") or Path.cwd())
        workspace = _ask("Workspace directory", existing)
        root = Path(workspace).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"workspace directory does not exist: {root}")
        workspace = str(root)
    apply_mode(profile, mode, workspace)
    if mode != "chat_only":
        policy = _ask("Context policy (adaptive/snapshot)", str(profile.get("context_policy", "adaptive"))).lower()
        if policy not in {"adaptive", "snapshot"}:
            raise ValueError("context policy must be adaptive or snapshot")
        profile["context_policy"] = policy
    if mode == "full_workspace":
        print("\nRecommended FULL WORKSPACE safety defaults:")
        print("  - guarded executable allowlist")
        print("  - 6 model/tool iterations")
        print("  - 30 second command timeout")
        print("  - 64 KiB captured command output")
        print("  - hidden/vendor directories excluded from discovery")
        if _yes(_ask("Use recommended FULL WORKSPACE limits? (yes/no)", "yes")):
            profile["tool_mode"] = "guarded"
            profile["workspace_max_steps"] = 6
            profile["workspace_max_actions_per_step"] = 8
            profile["exec_timeout_seconds"] = 30
            profile["workspace_max_file_bytes"] = 262144
            profile["workspace_max_output_bytes"] = 65536
            profile["workspace_include_hidden"] = False
    return profile


def cmd_guided(args: argparse.Namespace) -> int:
    name = args.name or _ask("Profile name", "default")
    try:
        p = load_profile(name)
    except FileNotFoundError:
        p = default_profile(name)
        p["port"] = _next_free_profile_port("port", 1235)
        p["upstream_port"] = _next_free_profile_port("upstream_port", 13335)
        p["mcp_port"] = _next_free_profile_port("mcp_port", 1236)
        p["cdp_port"] = _next_free_profile_port("cdp_port", 9232)
    print("\nchatgptproxy guided configuration")
    print("The recommended path is to choose a product mode first and keep transport/session defaults unless you have a port conflict.")
    _configure_mode_interactive(p)
    p["default_model"] = _ask("Default ChatGPT model", str(p.get("default_model", "auto")))
    p["session_supervisor_enabled"] = _yes(_ask("Enable session/auth supervisor? (yes/no)", "yes"))
    p["auto_recover_browser"] = _yes(_ask("Automatically recover dead/broken browser? (yes/no)", "yes"))
    if _yes(_ask("Configure advanced ports/browser settings? (yes/no)", "no")):
        _advanced_interactive(p)
    save_profile(p, overwrite=args.overwrite or Path(browser_dir(name)).exists() or name in list_profiles())
    print(f"\nSaved profile: {name}")
    print(f"Mode: {mode_label(str(p['workspace_mode']))}")
    if p.get("workspace_path"):
        print(f"Workspace: {p['workspace_path']}")
    print(f"Browser profile: {browser_dir(name)}")
    print(f"Public API: http://{p['host']}:{p['port']}/v1")
    print(f"Internal upstream: http://{p.get('upstream_host', '127.0.0.1')}:{p.get('upstream_port', 13335)}")
    print(f"API key: {p['api_key']}")
    return 0


def _advanced_interactive(p: dict[str, Any]) -> None:
    print("\nAdvanced configuration")
    p["host"] = _ask("Public gateway bind host", str(p["host"]))
    p["port"] = int(_ask("Public REST port", str(p["port"])))
    p["upstream_port"] = int(_ask("Internal ChatGPT-Web2API port", str(p.get("upstream_port", 13335))))
    p["cdp_port"] = int(_ask("Chrome CDP port", str(p["cdp_port"])))
    p["mcp_port"] = int(_ask("MCP SSE port", str(p["mcp_port"])))
    p["headless"] = _yes(_ask("Headless Chrome? (yes/no)", "yes" if p.get("headless") else "no"))
    p["mcp_enabled"] = _yes(_ask("Start upstream MCP too? (yes/no)", "yes" if p.get("mcp_enabled") else "no"))
    p["auth_check_interval_seconds"] = int(_ask("Auth validation interval (seconds)", str(p.get("auth_check_interval_seconds", 300))))
    p["auth_refresh_lead_seconds"] = int(_ask("Refresh when token TTL is below (seconds)", str(p.get("auth_refresh_lead_seconds", 600))))
    if normalize_mode(str(p.get("workspace_mode", "chat_only"))) == "full_workspace":
        p["workspace_max_steps"] = int(_ask("Max workspace tool iterations", str(p.get("workspace_max_steps", 6))))
        p["exec_timeout_seconds"] = int(_ask("Max command timeout (seconds)", str(p.get("exec_timeout_seconds", 30))))
        p["tool_mode"] = _ask("Command policy (guarded/unrestricted)", str(p.get("tool_mode", "guarded"))).lower()
        if p["tool_mode"] not in {"guarded", "unrestricted"}:
            raise ValueError("tool mode must be guarded or unrestricted")


def _menu_auth(active: str) -> None:
    while True:
        print(f"\nAuthentication — {active}")
        print("  1. Open ChatGPT login")
        print("  2. Validate session")
        print("  3. Refresh/validate session")
        print("  4. Show auth state")
        print("  0. Back")
        choice = input("Select: ").strip()
        if choice == "0":
            return
        mapping = {"1": "login", "2": "validate", "3": "refresh", "4": "status"}
        command = mapping.get(choice)
        if not command:
            print("Unknown choice")
            continue
        cmd_auth(argparse.Namespace(auth_cmd=command, profile=active, check=(command == "status")))


def _menu_profiles(active: str) -> str:
    while True:
        print(f"\nProfiles — active: {active}")
        for name in list_profiles():
            mark = "*" if name == active else " "
            p = load_profile(name)
            print(f" {mark} {name:18} {mode_label(str(p.get('workspace_mode', 'chat_only')))}")
        print("\n  1. Switch profile")
        print("  2. Create/configure profile")
        print("  3. Clone profile")
        print("  4. Delete profile")
        print("  0. Back")
        choice = input("Select: ").strip()
        if choice == "0":
            return active
        if choice == "1":
            name = _ask("Profile name", active)
            if name not in list_profiles():
                print("Profile not found")
            else:
                active = name
        elif choice == "2":
            name = _ask("New profile name", "project")
            cmd_guided(argparse.Namespace(name=name, overwrite=True))
            active = name
        elif choice == "3":
            source = _ask("Source", active)
            target = _ask("Target", source + "-copy")
            cmd_profile(argparse.Namespace(profile_cmd="clone", source=source, target=target))
            active = target
        elif choice == "4":
            name = _ask("Profile to delete", active)
            if _yes(_ask(f"Delete {name}? (yes/no)", "no")):
                cmd_profile(argparse.Namespace(profile_cmd="delete", name=name))
                active = "default" if "default" in list_profiles() else (list_profiles()[0] if list_profiles() else "default")
        else:
            print("Unknown choice")


def _menu_advanced(active: str) -> None:
    while True:
        print(f"\nAdvanced — {active}")
        print("  1. Edit advanced profile settings")
        print("  2. Browser status")
        print("  3. Restart managed browser/upstream")
        print("  4. Run one supervisor check")
        print("  5. Show raw profile")
        print("  0. Back")
        choice = input("Select: ").strip()
        if choice == "0":
            return
        if choice == "1":
            p = _profile(active)
            _advanced_interactive(p)
            save_profile(p)
            print("Saved.")
        elif choice == "2":
            cmd_browser(argparse.Namespace(browser_cmd="status", profile=active))
        elif choice == "3":
            cmd_browser(argparse.Namespace(browser_cmd="restart", profile=active))
        elif choice == "4":
            cmd_session(argparse.Namespace(session_cmd="keepalive", profile=active, once=True, force=True))
        elif choice == "5":
            cmd_profile(argparse.Namespace(profile_cmd="show", name=active, show_key=False))
        else:
            print("Unknown choice")


def cmd_menu(args: argparse.Namespace) -> int:
    ensure_default_profile()
    active = getattr(args, "profile", None) or "default"
    if active not in list_profiles():
        active = "default"
    while True:
        p = _profile(active)
        mode = mode_label(str(p.get("workspace_mode", "chat_only")))
        print("\n" + "=" * 62)
        print(f"chatgptproxy {__version__}")
        print(f"Active profile: {active} | {mode}")
        if p.get("workspace_path"):
            print(f"Workspace: {p['workspace_path']}")
        print("=" * 62)
        print("  1. Start profile")
        print("  2. Stop profile")
        print("  3. Status")
        print("  4. Configure operating mode  [recommended]")
        print("  5. ChatGPT login / session")
        print("  6. Show client configuration")
        print("  7. Doctor")
        print("  8. Profiles")
        print("  9. Advanced")
        print("  0. Exit")
        choice = input("Select: ").strip()
        try:
            if choice == "0":
                return 0
            if choice == "1":
                cmd_start(argparse.Namespace(profile=active, mcp=None, supervisor=None))
            elif choice == "2":
                cmd_stop(argparse.Namespace(profile=active))
            elif choice == "3":
                cmd_status(argparse.Namespace(profile=active, json=False, check_auth=False))
            elif choice == "4":
                p = _profile(active)
                _configure_mode_interactive(p)
                save_profile(p)
                print("Saved. Restart the profile if it is currently running.")
            elif choice == "5":
                _menu_auth(active)
            elif choice == "6":
                cmd_endpoint(argparse.Namespace(profile=active))
            elif choice == "7":
                cmd_doctor(argparse.Namespace(profile=active, upstream=False))
            elif choice == "8":
                active = _menu_profiles(active)
            elif choice == "9":
                _menu_advanced(active)
            else:
                print("Unknown choice")
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)


def cmd_configure(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    mode = normalize_mode(args.mode)
    workspace = args.workspace
    if mode != "chat_only" and not workspace:
        workspace = p.get("workspace_path") or str(Path.cwd())
    apply_mode(p, mode, str(workspace) if workspace else None)
    if args.context_policy:
        if args.context_policy not in {"adaptive", "snapshot"}:
            raise ValueError("context policy must be adaptive or snapshot")
        p["context_policy"] = args.context_policy
    save_profile(p)
    print(f"Configured {p['name']}: {mode_label(mode)}")
    if p.get("workspace_path"):
        print(f"Workspace: {p['workspace_path']}")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_cmd == "list":
        for name in list_profiles():
            p = load_profile(name)
            print(f"{name}\t{mode_label(str(p.get('workspace_mode', 'chat_only')))}\t{p.get('workspace_path') or '-'}")
        return 0
    if args.profile_cmd == "show":
        p = load_profile(args.name)
        if not args.show_key:
            p = dict(p)
            p["api_key"] = "***redacted***"
        print_json(p)
        return 0
    if args.profile_cmd == "clone":
        p = clone_profile(args.source, args.target)
        print(f"Cloned {args.source} -> {args.target}")
        print(f"New API key: {p['api_key']}")
        return 0
    if args.profile_cmd == "delete":
        delete_profile(args.name)
        print(f"Deleted profile: {args.name}")
        return 0
    raise RuntimeError("unknown profile command")


def cmd_serve(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    validate_workspace_profile(p)
    return run_foreground(p)


def cmd_ensure(args: argparse.Namespace) -> int:
    return run_ensure(_profile(args.profile))


def cmd_start(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    validate_workspace_profile(p)
    result = start_background(p, with_mcp=args.mcp, with_supervisor=args.supervisor)
    print_json(result)
    print(f"Gateway log: {logs_dir() / (p['name'] + '.gateway.log')}")
    print(f"Upstream log: {logs_dir() / (p['name'] + '.upstream.log')}")
    if "supervisor_pid" in result:
        print(f"Supervisor log: {logs_dir() / (p['name'] + '.supervisor.log')}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    stopped = stop_background(p)
    if stopped:
        print("Stopped: " + ", ".join(map(str, stopped)))
    else:
        print("No managed process was running")
    return 0


def _print_session_state(state: dict[str, Any]) -> None:
    print("\nAUTH")
    print(f"State: {state.get('auth_state', 'UNKNOWN')}")
    print(f"Login required: {'yes' if state.get('needs_login') else 'no'}")
    print(f"Token TTL: {format_duration(state.get('token_ttl_seconds'))}")
    print(f"Token expires: {state.get('token_expires_at') or '-'}")
    print(f"Last auth check: {state.get('last_auth_check_at') or '-'}")
    print(f"Last refresh: {state.get('last_auth_refresh_at') or '-'}")
    print("\nSESSION SUPERVISOR")
    print(f"Browser state: {state.get('browser_state', 'UNKNOWN')}")
    print(f"Upstream state: {state.get('upstream_status') or '-'}")
    print(f"Last successful send: {state.get('last_successful_send_at') or '-'}")
    print(f"Auth failures: {state.get('consecutive_auth_failures', 0)}")
    print(f"Health failures: {state.get('consecutive_health_failures', 0)}")
    print(f"Last recovery: {state.get('last_browser_recovery_at') or '-'}")
    if state.get("last_error"):
        print(f"Last error: {state['last_error']}")


def cmd_status(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    if getattr(args, "check_auth", False):
        session_check(p)
    snapshot = status_snapshot(p)
    state = load_state(str(p["name"]))
    snapshot["session"] = state
    if args.json:
        print_json(snapshot)
    else:
        print(f"Profile: {snapshot['profile']}")
        print(f"Mode: {mode_label(str(p.get('workspace_mode', 'chat_only')))}")
        print(f"Workspace: {p.get('workspace_path') or '-'}")
        print(f"Public endpoint: {snapshot['base_url']}")
        print(f"Internal upstream: {snapshot['upstream_url']}")
        print(f"Gateway PID: {snapshot.get('gateway_pid') or '-'} ({'running' if snapshot.get('gateway_alive') else 'stopped'})")
        print(f"Upstream PID: {snapshot['pid'] or '-'} ({'running' if snapshot['process_alive'] else 'stopped'})")
        print(f"MCP PID: {snapshot.get('mcp_pid') or '-'} ({'running' if snapshot.get('mcp_alive') else 'stopped'})")
        print(f"Supervisor PID: {snapshot.get('supervisor_pid') or '-'} ({'running' if snapshot.get('supervisor_alive') else 'stopped'})")
        print(f"Gateway health: {snapshot.get('gateway_http_status') or 'unavailable'}")
        if snapshot.get("http_status") is not None:
            print(f"Upstream health HTTP: {snapshot['http_status']}")
            body = snapshot.get("health")
            if isinstance(body, dict):
                print(f"Upstream status: {body.get('status', 'unknown')}")
                print(f"Chrome: {body.get('chrome_running', 'unknown')}")
                print(f"Driver: {body.get('driver_connected', 'unknown')}")
                print(f"Open breakers: {body.get('open_breakers', [])}")
        else:
            print(f"Upstream health: unavailable ({snapshot.get('health_error', 'connection failed')})")
        _print_session_state(state)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    try:
        code, body = models(p)
    except ConnectionError as exc:
        print(f"Probe failed: {exc}", file=sys.stderr)
        return 1
    print(f"HTTP {code}")
    print_json(body)
    return 0 if code == 200 else 1


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((host, port)) != 0
    except OSError:
        return False


def cmd_doctor(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), platform.python_version()))
    up = upstream_binary()
    checks.append(("chatgpt-web2api", bool(up), up or "not found on PATH"))
    mcp = upstream_mcp_binary()
    checks.append(("chatgpt-web2api-mcp", bool(mcp), mcp or "not found on PATH"))
    chrome = find_chrome_binary(p.get("chrome_path"))
    checks.append(("Chrome/Chromium", bool(chrome), chrome or "not found on PATH (upstream may still auto-detect GUI installs)"))
    try:
        validate_workspace_profile(p)
        workspace_ok, workspace_detail = True, mode_label(str(p.get("workspace_mode", "chat_only"))) + (f" @ {p.get('workspace_path')}" if p.get("workspace_path") else "")
    except Exception as exc:
        workspace_ok, workspace_detail = False, str(exc)
    checks.append(("Workspace configuration", workspace_ok, workspace_detail))
    checks.append(("Public gateway port", True, f"{p['host']}:{p['port']} {'free' if _port_free(p['host'], int(p['port'])) else 'in use'}"))
    checks.append(("Internal upstream port", True, f"{p.get('upstream_host', '127.0.0.1')}:{p.get('upstream_port', 13335)} {'free' if _port_free(str(p.get('upstream_host', '127.0.0.1')), int(p.get('upstream_port', 13335))) else 'in use'}"))
    snapshot = status_snapshot(p)
    upstream_healthy = snapshot.get("http_status") == 200 and isinstance(snapshot.get("health"), dict)
    gateway_healthy = snapshot.get("gateway_http_status") == 200
    checks.append(("Gateway health reachable", gateway_healthy, str(snapshot.get("gateway_health", snapshot.get("gateway_health_error", "unreachable")))))
    checks.append(("Upstream health reachable", upstream_healthy, str(snapshot.get("health", snapshot.get("health_error", "unreachable")))))
    if snapshot.get("process_alive"):
        try:
            browser = cdp.status(int(p["cdp_port"]))
            checks.append(("Chrome CDP", True, str(browser)))
        except cdp.CDPError as exc:
            checks.append(("Chrome CDP", False, str(exc)))
    state = load_state(str(p["name"]))
    auth_ok = state.get("auth_state") in {"READY", "REFRESH_SOON"}
    checks.append(("Cached auth state", auth_ok, str(state.get("auth_state", "UNKNOWN"))))
    failed = 0
    for label, ok, detail in checks:
        print(f"{'OK' if ok else 'WARN'}  {label}: {detail}")
        failed += 0 if ok else 1
    if args.upstream and up:
        print("\n--- upstream doctor ---")
        subprocess.run([up, "doctor"], check=False)
    return 0 if failed == 0 else 1


def cmd_key(args: argparse.Namespace) -> int:
    print(_profile(args.profile)["api_key"])
    return 0


def cmd_endpoint(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    print(f"Base URL: http://{p['host']}:{p['port']}/v1")
    print(f"API key:  {p['api_key']}")
    print(f"Model:    {p.get('default_model', 'auto')}")
    print(f"Mode:     {mode_label(str(p.get('workspace_mode', 'chat_only')))}")
    if p.get("workspace_path"):
        print(f"Workspace: {p['workspace_path']}")
    print("\nOpenAI SDK:")
    print("from openai import OpenAI")
    print(f"client = OpenAI(base_url='http://{p['host']}:{p['port']}/v1', api_key='{p['api_key']}')")
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    if args.workspace_cmd == "status":
        value = {
            "profile": p["name"],
            "mode": normalize_mode(str(p.get("workspace_mode", "chat_only"))),
            "mode_label": mode_label(str(p.get("workspace_mode", "chat_only"))),
            "workspace_path": p.get("workspace_path"),
            "context_policy": p.get("context_policy"),
            "write_enabled": p.get("workspace_write_enabled"),
            "exec_enabled": p.get("exec_enabled"),
            "tool_mode": p.get("tool_mode"),
            "max_steps": p.get("workspace_max_steps"),
            "exec_timeout_seconds": p.get("exec_timeout_seconds"),
        }
        print_json(value)
        return 0
    if args.workspace_cmd == "test":
        validate_workspace_profile(p)
        mode = normalize_mode(str(p.get("workspace_mode", "chat_only")))
        if mode == "chat_only":
            print_json({"ok": True, "mode": mode, "message": "CHAT ONLY has no workspace access"})
            return 0
        executor = WorkspaceExecutor(p)
        result = executor.list(".", depth=1, limit=30)
        print_json({"ok": True, "mode": mode, "workspace": str(executor.root), "sample": result})
        return 0
    raise RuntimeError("unknown workspace command")


def cmd_auth(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    name = str(p["name"])
    if args.auth_cmd == "status":
        state = session_check(p) if getattr(args, "check", False) else load_state(name)
        print_json(state)
        return 0 if state.get("auth_state") in {"READY", "REFRESH_SOON"} else 1
    if args.auth_cmd == "validate":
        state = session_check(p, refresh=False)
        print_json(state)
        return 0 if state.get("auth_state") in {"READY", "REFRESH_SOON"} else 1
    if args.auth_cmd == "refresh":
        state = session_check(p, refresh=True)
        if state.get("auth_state") in {"READY", "REFRESH_SOON"} and "auth_required" in (state.get("open_breakers") or []):
            rc = run_ensure(p)
            if rc == 0:
                state = session_check(p, refresh=False)
        print_json(state)
        return 0 if state.get("auth_state") in {"READY", "REFRESH_SOON"} else 1
    if args.auth_cmd == "login":
        upstream_pid = read_pid(pid_path(name))
        if not upstream_pid or not process_alive(upstream_pid):
            print(f"Starting proxy stack for profile '{name}'...", file=sys.stderr)
            start_background(p)
            time.sleep(2)
        value = {}
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                value = cdp.open_chatgpt(int(p["cdp_port"]))
                break
            except cdp.CDPError:
                time.sleep(1.0)
        if not value:
            value = cdp.open_chatgpt(int(p["cdp_port"]))
        state = load_state(name)
        state["auth_state"] = "NEEDS_LOGIN"
        state["needs_login"] = True
        save_state(name, state)
        print(f"Opened ChatGPT in the managed browser for profile {name}.")
        if value.get("url"):
            print(f"Tab: {value['url']}")
        print(f"After login, run: chatgptproxy auth validate {name}")
        return 0
    raise RuntimeError("unknown auth command")


def cmd_session(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    name = str(p["name"])
    if args.session_cmd == "status":
        state = session_check(p) if getattr(args, "check", False) else load_state(name)
        print_json(state)
        return 0
    if args.session_cmd == "check":
        print_json(session_check(p))
        return 0
    if args.session_cmd == "keepalive":
        existing = read_pid(supervisor_pid_path(name))
        if existing and process_alive(existing) and not args.once and not args.force:
            raise RuntimeError(f"session supervisor is already running for {name} (pid {existing}); use --force to run another foreground loop")
        return run_loop(name, once=args.once)
    raise RuntimeError("unknown session command")


def cmd_browser(args: argparse.Namespace) -> int:
    p = _profile(args.profile)
    if args.browser_cmd == "status":
        try:
            print_json(cdp.status(int(p["cdp_port"])))
            return 0
        except cdp.CDPError as exc:
            print_json({"reachable": False, "error": str(exc)})
            return 1
    if args.browser_cmd == "restart":
        result = recover_managed_stack(p, reason="manual_browser_restart")
        state = load_state(str(p["name"]))
        state["last_browser_recovery_at"] = iso_now()
        state["last_recovery_reason"] = "manual_browser_restart"
        save_state(str(p["name"]), state)
        print_json(result)
        return 0
    raise RuntimeError("unknown browser command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatgptproxy", description="managed ChatGPT-Web2API gateway with profiles, session lifecycle and local workspace modes")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("guided", help="recommended interactive profile configuration")
    p.add_argument("name", nargs="?")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_guided)

    p = sub.add_parser("configure", help="set product mode non-interactively")
    p.add_argument("profile", nargs="?", default="default")
    p.add_argument("--mode", required=True, help="chat_only, read_only, or full_workspace")
    p.add_argument("--workspace")
    p.add_argument("--context-policy", choices=["adaptive", "snapshot"])
    p.set_defaults(func=cmd_configure)

    p = sub.add_parser("menu", help="interactive operations/configuration menu")
    p.add_argument("profile", nargs="?", default="default")
    p.set_defaults(func=cmd_menu)

    p = sub.add_parser("profile", help="manage named profiles")
    sp = p.add_subparsers(dest="profile_cmd", required=True)
    q = sp.add_parser("list"); q.set_defaults(func=cmd_profile)
    q = sp.add_parser("show"); q.add_argument("name"); q.add_argument("--show-key", action="store_true"); q.set_defaults(func=cmd_profile)
    q = sp.add_parser("clone"); q.add_argument("source"); q.add_argument("target"); q.set_defaults(func=cmd_profile)
    q = sp.add_parser("delete"); q.add_argument("name"); q.set_defaults(func=cmd_profile)

    for name, func, help_text in (
        ("serve", cmd_serve, "run upstream plus public gateway in foreground"),
        ("ensure", cmd_ensure, "run upstream REST+MCP readiness reconciliation"),
        ("stop", cmd_stop, "stop managed background processes"),
        ("key", cmd_key, "print profile API key"),
        ("endpoint", cmd_endpoint, "print client connection settings"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("profile", nargs="?", default="default")
        p.set_defaults(func=func)

    p = sub.add_parser("start", help="start public gateway, ChatGPT-Web2API and session supervisor")
    p.add_argument("profile", nargs="?", default="default")
    g = p.add_mutually_exclusive_group(); g.add_argument("--mcp", dest="mcp", action="store_true"); g.add_argument("--no-mcp", dest="mcp", action="store_false")
    sg = p.add_mutually_exclusive_group(); sg.add_argument("--supervisor", dest="supervisor", action="store_true"); sg.add_argument("--no-supervisor", dest="supervisor", action="store_false")
    p.set_defaults(mcp=None, supervisor=None, func=cmd_start)

    p = sub.add_parser("status", help="show gateway, upstream, browser, workspace and session health")
    p.add_argument("profile", nargs="?", default="default"); p.add_argument("--json", action="store_true"); p.add_argument("--check-auth", action="store_true"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("probe", help="probe the public OpenAI-compatible model endpoint"); p.add_argument("profile", nargs="?", default="default"); p.set_defaults(func=cmd_probe)
    p = sub.add_parser("doctor", help="check local dependencies, workspace and health"); p.add_argument("profile", nargs="?", default="default"); p.add_argument("--upstream", action="store_true"); p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("workspace", help="inspect local workspace configuration")
    sp = p.add_subparsers(dest="workspace_cmd", required=True)
    q = sp.add_parser("status"); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_workspace)
    q = sp.add_parser("test"); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_workspace)

    p = sub.add_parser("auth", help="inspect and recover ChatGPT browser authentication")
    sp = p.add_subparsers(dest="auth_cmd", required=True)
    q = sp.add_parser("status"); q.add_argument("profile", nargs="?", default="default"); q.add_argument("--check", action="store_true"); q.set_defaults(func=cmd_auth)
    for command, help_text in (("validate", "validate the browser login"), ("refresh", "refresh/validate via /api/auth/session"), ("login", "open ChatGPT in the managed browser")):
        q = sp.add_parser(command, help=help_text); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_auth)

    p = sub.add_parser("session", help="manage the TTL/liveness supervisor")
    sp = p.add_subparsers(dest="session_cmd", required=True)
    q = sp.add_parser("status"); q.add_argument("profile", nargs="?", default="default"); q.add_argument("--check", action="store_true"); q.set_defaults(func=cmd_session)
    q = sp.add_parser("check"); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_session)
    q = sp.add_parser("keepalive"); q.add_argument("profile", nargs="?", default="default"); q.add_argument("--once", action="store_true"); q.add_argument("--force", action="store_true"); q.set_defaults(func=cmd_session)

    p = sub.add_parser("browser", help="inspect or recover managed Chrome/CDP")
    sp = p.add_subparsers(dest="browser_cmd", required=True)
    q = sp.add_parser("status"); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_browser)
    q = sp.add_parser("restart"); q.add_argument("profile", nargs="?", default="default"); q.set_defaults(func=cmd_browser)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args) or 0)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, cdp.CDPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

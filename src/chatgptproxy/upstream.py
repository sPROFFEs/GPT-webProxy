from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .httpclient import json_request
from .paths import ensure_dirs, logs_dir, runtime_dir
from .profiles import write_runtime_config


def upstream_binary() -> str | None:
    found = shutil.which("chatgpt-web2api")
    if found:
        return found
    venv_bin = Path(sys.executable).parent / ("chatgpt-web2api.exe" if os.name == "nt" else "chatgpt-web2api")
    if venv_bin.exists() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    prefix_bin = Path(sys.prefix) / "bin" / ("chatgpt-web2api.exe" if os.name == "nt" else "chatgpt-web2api")
    if prefix_bin.exists() and os.access(prefix_bin, os.X_OK):
        return str(prefix_bin)
    return None


def upstream_mcp_binary() -> str | None:
    found = shutil.which("chatgpt-web2api-mcp")
    if found:
        return found
    venv_bin = Path(sys.executable).parent / ("chatgpt-web2api-mcp.exe" if os.name == "nt" else "chatgpt-web2api-mcp")
    if venv_bin.exists() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    prefix_bin = Path(sys.prefix) / "bin" / ("chatgpt-web2api-mcp.exe" if os.name == "nt" else "chatgpt-web2api-mcp")
    if prefix_bin.exists() and os.access(prefix_bin, os.X_OK):
        return str(prefix_bin)
    return None


def pid_path(profile_name: str) -> Path:
    return runtime_dir() / f"{profile_name}.pid"


def gateway_pid_path(profile_name: str) -> Path:
    return runtime_dir() / f"{profile_name}.gateway.pid"


def mcp_pid_path(profile_name: str) -> Path:
    return runtime_dir() / f"{profile_name}.mcp.pid"


def supervisor_pid_path(profile_name: str) -> Path:
    return runtime_dir() / f"{profile_name}.supervisor.pid"


def _env(profile: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    venv_dir = str(Path(sys.executable).parent)
    if venv_dir not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = f"{venv_dir}{os.pathsep}{env.get('PATH', '')}"
    env["W2A_LOG_LEVEL"] = str(profile.get("log_level", "INFO"))
    if profile.get("diagnose"):
        env["W2A_DIAGNOSE"] = "1"
    if profile.get("mcp_write"):
        env["W2A_ENABLE_WRITE"] = "1"
    if profile.get("mcp_destructive"):
        env["W2A_ENABLE_DESTRUCTIVE"] = "1"
    return env


def rest_command(profile: dict[str, Any]) -> list[str]:
    binary = upstream_binary()
    if not binary:
        raise RuntimeError("chatgpt-web2api is not installed or not on PATH")
    cfg = write_runtime_config(profile)
    return [binary, "--config", str(cfg)]


def gateway_command(profile: dict[str, Any]) -> list[str]:
    return [sys.executable, "-m", "chatgptproxy.gateway", "--profile", str(profile["name"])]


def ensure_command(profile: dict[str, Any]) -> list[str]:
    binary = upstream_binary()
    if not binary:
        raise RuntimeError("chatgpt-web2api is not installed or not on PATH")
    cfg = write_runtime_config(profile)
    return [
        binary,
        "ensure",
        "--rest-port", str(profile.get("upstream_port", 13335)),
        "--mcp-sse-port", str(profile["mcp_port"]),
        "--cdp-port", str(profile["cdp_port"]),
        "--config", str(cfg),
    ]


def mcp_command(profile: dict[str, Any]) -> list[str]:
    binary = upstream_mcp_binary()
    if not binary:
        raise RuntimeError("chatgpt-web2api-mcp is not installed or not on PATH")
    return [
        binary,
        "--transport", "sse",
        "--port", str(profile["mcp_port"]),
        "--cdp-port", str(profile["cdp_port"]),
    ]


def supervisor_command(profile: dict[str, Any]) -> list[str]:
    return [sys.executable, "-m", "chatgptproxy.supervisor", "--profile", str(profile["name"])]


def run_foreground(profile: dict[str, Any]) -> int:
    name = str(profile["name"])
    if read_pid(pid_path(name)) and process_alive(read_pid(pid_path(name)) or -1):
        raise RuntimeError(f"profile {name} already has a managed upstream process")
    upstream_pid = start_rest_process(profile)
    try:
        return subprocess.call(gateway_command(profile), env=_env(profile))
    finally:
        if process_alive(upstream_pid):
            _terminate(upstream_pid)
            _wait_dead(upstream_pid, timeout=5.0)
        pid_path(name).unlink(missing_ok=True)


def run_ensure(profile: dict[str, Any]) -> int:
    return subprocess.call(ensure_command(profile), env=_env(profile))


def _popen_detached(cmd: list[str], log: Path, env: dict[str, str]) -> subprocess.Popen[Any]:
    ensure_dirs()
    handle = log.open("ab", buffering=0)
    kwargs: dict[str, Any] = {"stdout": handle, "stderr": subprocess.STDOUT, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    handle.close()
    return proc


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        if os.name != "nt" and Path(f"/proc/{pid}/status").exists():
            try:
                status_text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="ignore")
                for line in status_text.splitlines():
                    if line.startswith("State:"):
                        state = line.split(":", 1)[1].strip().upper()
                        if state.startswith("Z"):
                            return False
            except OSError:
                return False
        return True
    except OSError:
        return False


def _terminate(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _wait_dead(pid: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.2)
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def start_rest_process(profile: dict[str, Any]) -> int:
    name = str(profile["name"])
    existing = read_pid(pid_path(name))
    if existing and process_alive(existing):
        return existing
    rest_log = logs_dir() / f"{name}.upstream.log"
    rest = _popen_detached(rest_command(profile), rest_log, _env(profile))
    pid_path(name).write_text(str(rest.pid), encoding="ascii")
    return rest.pid


def start_gateway_process(profile: dict[str, Any]) -> int:
    name = str(profile["name"])
    existing = read_pid(gateway_pid_path(name))
    if existing and process_alive(existing):
        return existing
    log = logs_dir() / f"{name}.gateway.log"
    proc = _popen_detached(gateway_command(profile), log, _env(profile))
    gateway_pid_path(name).write_text(str(proc.pid), encoding="ascii")
    return proc.pid


def _wait_browser_ready(profile: dict[str, Any], timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, body = health(profile)
            if (
                code == 200
                and isinstance(body, dict)
                and body.get("chrome_running") is True
                and body.get("driver_connected") is True
            ):
                return True
        except ConnectionError:
            pass
        time.sleep(1.0)
    return False


def start_mcp_process(profile: dict[str, Any]) -> int:
    name = str(profile["name"])
    if not _wait_browser_ready(profile, timeout=30.0):
        raise RuntimeError(
            "upstream started but Chrome/driver is not ready for MCP. "
            f"Complete browser login if necessary, then run `chatgptproxy ensure {name}`."
        )
    mcp_log = logs_dir() / f"{name}.mcp.log"
    mcp = _popen_detached(mcp_command(profile), mcp_log, _env(profile))
    mcp_pid_path(name).write_text(str(mcp.pid), encoding="ascii")
    return mcp.pid


def start_supervisor_process(profile: dict[str, Any]) -> int:
    name = str(profile["name"])
    existing = read_pid(supervisor_pid_path(name))
    if existing and process_alive(existing):
        return existing
    log = logs_dir() / f"{name}.supervisor.log"
    proc = _popen_detached(supervisor_command(profile), log, _env(profile))
    supervisor_pid_path(name).write_text(str(proc.pid), encoding="ascii")
    return proc.pid


def start_background(
    profile: dict[str, Any], *, with_mcp: bool | None = None, with_supervisor: bool | None = None
) -> dict[str, int]:
    ensure_dirs()
    name = str(profile["name"])
    result = {"upstream_pid": start_rest_process(profile)}
    try:
        result["gateway_pid"] = start_gateway_process(profile)
        use_mcp = bool(profile.get("mcp_enabled")) if with_mcp is None else with_mcp
        if use_mcp:
            result["mcp_pid"] = start_mcp_process(profile)
        use_supervisor = bool(profile.get("session_supervisor_enabled", True)) if with_supervisor is None else with_supervisor
        if use_supervisor:
            result["supervisor_pid"] = start_supervisor_process(profile)
        return result
    except Exception:
        stop_background(profile)
        raise


def stop_background(profile: dict[str, Any]) -> list[int]:
    stopped: list[int] = []
    name = str(profile["name"])
    # Stop the supervisor first so it cannot race the intentional shutdown.
    for path in (supervisor_pid_path(name), gateway_pid_path(name), mcp_pid_path(name), pid_path(name)):
        pid = read_pid(path)
        if pid and process_alive(pid):
            _terminate(pid)
            _wait_dead(pid, timeout=5.0)
            stopped.append(pid)
        path.unlink(missing_ok=True)
    return stopped


def recover_managed_stack(profile: dict[str, Any], *, reason: str = "manual") -> dict[str, int | str]:
    """Restart ChatGPT-Web2API-owned Chrome without touching gateway/supervisor."""
    name = str(profile["name"])
    try:
        code, body = health(profile)
        if code == 200 and isinstance(body, dict) and "auth_required" in (body.get("open_breakers") or []):
            raise RuntimeError("authentication is required; browser restart would not repair the login")
    except ConnectionError:
        pass

    for path in (mcp_pid_path(name), pid_path(name)):
        pid = read_pid(path)
        if pid and process_alive(pid):
            _terminate(pid)
            _wait_dead(pid, timeout=5.0)
        path.unlink(missing_ok=True)

    result: dict[str, int | str] = {"reason": reason, "upstream_pid": start_rest_process(profile)}
    use_mcp = bool(profile.get("mcp_enabled"))
    if use_mcp:
        result["mcp_pid"] = start_mcp_process(profile)
    # Gateway is independent and normally survives upstream recovery. Restore it
    # only if it is unexpectedly absent.
    gateway_pid = read_pid(gateway_pid_path(name))
    if not gateway_pid or not process_alive(gateway_pid):
        result["gateway_pid"] = start_gateway_process(profile)
    return result


def health(profile: dict[str, Any]) -> tuple[int, Any]:
    url = f"http://{profile.get('upstream_host', '127.0.0.1')}:{profile.get('upstream_port', 13335)}/health"
    return json_request(url, api_key=str(profile["api_key"]))


def gateway_health(profile: dict[str, Any]) -> tuple[int, Any]:
    url = f"http://{profile['host']}:{profile['port']}/health"
    return json_request(url)


def models(profile: dict[str, Any]) -> tuple[int, Any]:
    # Probe the public gateway, not the hidden upstream service.
    url = f"http://{profile['host']}:{profile['port']}/v1/models"
    return json_request(url, api_key=str(profile["api_key"]))


def status_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    name = str(profile["name"])
    pid = read_pid(pid_path(name))
    gateway_pid = read_pid(gateway_pid_path(name))
    mcp_pid = read_pid(mcp_pid_path(name))
    supervisor_pid = read_pid(supervisor_pid_path(name))
    result: dict[str, Any] = {
        "profile": name,
        "pid": pid,
        "gateway_pid": gateway_pid,
        "mcp_pid": mcp_pid,
        "supervisor_pid": supervisor_pid,
        "process_alive": bool(pid and process_alive(pid)),
        "gateway_alive": bool(gateway_pid and process_alive(gateway_pid)),
        "mcp_alive": bool(mcp_pid and process_alive(mcp_pid)),
        "supervisor_alive": bool(supervisor_pid and process_alive(supervisor_pid)),
        "base_url": f"http://{profile['host']}:{profile['port']}/v1",
        "upstream_url": f"http://{profile.get('upstream_host', '127.0.0.1')}:{profile.get('upstream_port', 13335)}",
        "workspace_mode": profile.get("workspace_mode", "chat_only"),
        "workspace_path": profile.get("workspace_path"),
    }
    try:
        code, body = health(profile)
        result["http_status"] = code
        result["health"] = body
    except ConnectionError as exc:
        result["http_status"] = None
        result["health_error"] = str(exc)
    try:
        code, body = gateway_health(profile)
        result["gateway_http_status"] = code
        result["gateway_health"] = body
    except ConnectionError as exc:
        result["gateway_http_status"] = None
        result["gateway_health_error"] = str(exc)
    return result


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))

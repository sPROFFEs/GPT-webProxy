from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .session_state import parse_time, to_iso


class CDPError(RuntimeError):
    pass


def _json_get(url: str, timeout: float = 3.0) -> Any:
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else None
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        raise CDPError(str(exc)) from exc


def browser_version(cdp_port: int, timeout: float = 3.0) -> dict[str, Any]:
    value = _json_get(f"http://127.0.0.1:{int(cdp_port)}/json/version", timeout)
    if not isinstance(value, dict):
        raise CDPError("unexpected CDP /json/version response")
    return value


def targets(cdp_port: int, timeout: float = 3.0) -> list[dict[str, Any]]:
    value = _json_get(f"http://127.0.0.1:{int(cdp_port)}/json/list", timeout)
    if not isinstance(value, list):
        raise CDPError("unexpected CDP /json/list response")
    return [item for item in value if isinstance(item, dict)]


def select_chatgpt_target(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    pages = [item for item in items if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
    for item in pages:
        url = str(item.get("url") or "")
        if any(domain in url for domain in ("chatgpt.com", "chat.openai.com", "auth.openai.com", "auth0.openai.com", "login.live.com")):
            return item
    return pages[0] if pages else None


def _evaluate(ws_url: str, expression: str, timeout: float = 5.0) -> Any:
    try:
        import websocket  # type: ignore
    except ImportError as exc:
        raise CDPError("websocket-client is required for browser session inspection") from exc

    try:
        ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    except Exception as exc:  # websocket has multiple runtime exception classes
        raise CDPError(f"CDP websocket connection failed: {exc}") from exc

    request_id = 1
    try:
        ws.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        }))
        while True:
            raw = ws.recv()
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CDPError(f"Runtime.evaluate failed: {message['error']}")
            result = message.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise CDPError(str(result.get("description") or "browser evaluation failed"))
            return result.get("value")
    except CDPError:
        raise
    except Exception as exc:
        raise CDPError(f"CDP evaluation failed: {exc}") from exc
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _jwt_exp(access_token: str | None) -> str | None:
    if not access_token or access_token.count(".") < 2:
        return None
    try:
        payload = access_token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = decoded.get("exp")
        if not isinstance(exp, (int, float)):
            return None
        return to_iso(datetime.fromtimestamp(float(exp), timezone.utc))
    except Exception:
        return None


def auth_session(cdp_port: int, timeout: float = 8.0) -> dict[str, Any]:
    item = select_chatgpt_target(targets(cdp_port, timeout=min(timeout, 3.0)))
    if not item:
        raise CDPError("no browser page target is available")
    ws_url = str(item["webSocketDebuggerUrl"])
    expression = r"""
(async () => {
  try {
    const response = await fetch('/api/auth/session', {
      method: 'GET',
      credentials: 'include',
      cache: 'no-store',
      headers: {'Accept': 'application/json'}
    });
    const text = await response.text();
    let body = null;
    try { body = JSON.parse(text); } catch (_) {}
    return {ok: response.ok, status: response.status, body};
  } catch (error) {
    return {ok: false, status: 0, error: String(error)};
  }
})()
"""
    value = _evaluate(ws_url, expression, timeout=timeout)
    if not isinstance(value, dict):
        raise CDPError("unexpected auth-session probe result")
    body = value.get("body") if isinstance(value.get("body"), dict) else {}
    access_token = body.get("accessToken") if isinstance(body, dict) else None
    reported_expiry = body.get("expires") if isinstance(body, dict) else None
    jwt_expiry = _jwt_exp(access_token if isinstance(access_token, str) else None)
    token_expiry = jwt_expiry or (to_iso(parse_time(reported_expiry)) if parse_time(reported_expiry) else None)
    authenticated = bool(value.get("ok") and isinstance(access_token, str) and access_token)
    return {
        "authenticated": authenticated,
        "http_status": int(value.get("status") or 0),
        "token_expires_at": token_expiry,
        "reported_expires_at": to_iso(parse_time(reported_expiry)) if parse_time(reported_expiry) else None,
        "user_present": bool(isinstance(body, dict) and body.get("user")),
        "error": value.get("error"),
        "target_url": item.get("url"),
    }


def status(cdp_port: int, timeout: float = 3.0) -> dict[str, Any]:
    version = browser_version(cdp_port, timeout)
    items = targets(cdp_port, timeout)
    selected = select_chatgpt_target(items)
    return {
        "reachable": True,
        "browser": version.get("Browser"),
        "protocol_version": version.get("Protocol-Version"),
        "target_count": len(items),
        "chatgpt_target": selected.get("url") if selected else None,
    }


def open_chatgpt(cdp_port: int, timeout: float = 3.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{int(cdp_port)}/json/new?{quote('https://chatgpt.com/', safe=':/')}"
    req = Request(url, method="PUT", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            value = json.loads(raw) if raw else {}
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        raise CDPError(f"unable to open ChatGPT tab: {exc}") from exc
    return value if isinstance(value, dict) else {}

from __future__ import annotations

import argparse
import hmac
import json
import socket
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .profiles import load_profile
from .workspace import WorkspaceExecutor, normalize_mode, parse_tool_request, system_prompt


def _upstream_base(profile: dict[str, Any]) -> str:
    return f"http://{profile.get('upstream_host', '127.0.0.1')}:{int(profile.get('upstream_port', 13335))}"


def _request_upstream(
    profile: dict[str, Any], method: str, path: str, body: bytes | None = None, *, timeout: float | None = None
) -> tuple[int, dict[str, str], bytes]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {profile['api_key']}",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(_upstream_base(profile) + path, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout or float(profile.get("request_timeout", 120))) as response:
            raw = response.read()
            return response.status, dict(response.headers.items()), raw
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()
    except URLError as exc:
        raise ConnectionError(str(exc.reason)) from exc


def _extract_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content) if content is not None else ""


def _replace_content(payload: dict[str, Any], content: str) -> dict[str, Any]:
    value = json.loads(json.dumps(payload))
    choices = value.setdefault("choices", [{}])
    if not choices:
        choices.append({})
    message = choices[0].setdefault("message", {})
    message["role"] = message.get("role", "assistant")
    message["content"] = content
    choices[0]["finish_reason"] = "stop"
    return value


def _fallback_completion(request_body: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-cgp-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request_body.get("model", "auto"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


def _tool_loop(profile: dict[str, Any], request_body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    mode = normalize_mode(str(profile.get("workspace_mode", "chat_only")))
    if mode == "chat_only":
        raw = json.dumps(request_body).encode("utf-8")
        try:
            status, _, response = _request_upstream(profile, "POST", "/v1/chat/completions", raw)
        except ConnectionError as exc:
            return {"error": {"message": f"upstream connection failed: {exc}", "type": "upstream_unavailable"}}, 0
        if status != 200:
            try:
                return json.loads(response.decode("utf-8", errors="replace")), 0
            except json.JSONDecodeError:
                return {"error": response.decode("utf-8", errors="replace")}, 0
        return json.loads(response.decode("utf-8", errors="replace")), 0

    executor = WorkspaceExecutor(profile)
    messages = request_body.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    working = json.loads(json.dumps(request_body))
    working["stream"] = False
    working_messages = working["messages"]
    prompt = system_prompt(profile, executor)
    working_messages.insert(0, {"role": "system", "content": prompt})

    max_steps = max(1, min(int(profile.get("workspace_max_steps", 6)), 20))
    repaired = False
    last_response: dict[str, Any] | None = None
    for step in range(max_steps):
        raw = json.dumps(working).encode("utf-8")
        try:
            status, _, response = _request_upstream(profile, "POST", "/v1/chat/completions", raw)
        except ConnectionError as exc:
            return {"error": {"message": f"upstream connection failed: {exc}", "type": "upstream_unavailable"}}, step
        text = response.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"upstream returned non-JSON response (HTTP {status}): {text[:500]}")
        if status != 200:
            return payload, step
        last_response = payload
        content = _extract_content(payload)
        actions, parse_error = parse_tool_request(content)
        if parse_error:
            if repaired:
                return _replace_content(payload, f"Workspace tool request failed: {parse_error}"), step + 1
            repaired = True
            working_messages.append({"role": "assistant", "content": content})
            working_messages.append({
                "role": "user",
                "content": "The workspace tool request was malformed. Return ONLY one valid ```chatgpt-workspace JSON block with an object containing an actions array. Error: " + parse_error,
            })
            continue
        if actions is None:
            return payload, step

        result = executor.execute_actions(actions)
        feedback = json.dumps(result, ensure_ascii=False)
        max_feedback = max(4096, int(profile.get("workspace_max_tool_feedback_bytes", 131072)))
        if len(feedback.encode("utf-8")) > max_feedback:
            preview = feedback.encode("utf-8")[:max_feedback].decode("utf-8", errors="ignore")
            feedback = json.dumps({
                "workspace": str(executor.root),
                "mode": executor.mode,
                "truncated": True,
                "preview": preview,
            }, ensure_ascii=False)
        working_messages.append({"role": "assistant", "content": content})
        working_messages.append({
            "role": "user",
            "content": "chatgptproxy workspace tool results:\n```json\n" + feedback + "\n```\nContinue the task. If more real workspace state is needed, request another workspace tool step; otherwise provide the final answer.",
        })

    working_messages.append({
        "role": "user",
        "content": "The local workspace tool-step budget is exhausted. Do not request more workspace tools. Provide the best final answer using the verified results already returned.",
    })
    raw = json.dumps(working).encode("utf-8")
    try:
        status, _, response = _request_upstream(profile, "POST", "/v1/chat/completions", raw)
    except ConnectionError as exc:
        return {"error": {"message": f"upstream connection failed: {exc}", "type": "upstream_unavailable"}}, max_steps
    try:
        payload = json.loads(response.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        if last_response is not None:
            return _replace_content(last_response, _extract_content(last_response)), max_steps
        return _fallback_completion(request_body, "Workspace tool budget exhausted and the final upstream response could not be decoded."), max_steps
    return payload, max_steps


def _sse_chunks(payload: dict[str, Any], content: str) -> list[bytes]:
    completion_id = str(payload.get("id") or "chatcmpl-cgp-" + uuid.uuid4().hex)
    model = str(payload.get("model") or "auto")
    created = int(payload.get("created") or time.time())
    chunks: list[bytes] = []
    first = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    chunks.append(("data: " + json.dumps(first) + "\n\n").encode("utf-8"))
    size = 48
    for offset in range(0, len(content), size):
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content[offset:offset + size]}, "finish_reason": None}],
        }
        chunks.append(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    chunks.append(("data: " + json.dumps(final) + "\n\n").encode("utf-8"))
    chunks.append(b"data: [DONE]\n\n")
    return chunks


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "chatgptproxy-gateway/0.3"

    @property
    def profile(self) -> dict[str, Any]:
        return self.server.profile  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.profile['api_key']}"
        return hmac.compare_digest(auth, expected)

    def _validate_host_and_origin(self) -> bool:
        port = int(self.profile.get("port", 1235))
        cfg_host = str(self.profile.get("host", "127.0.0.1"))
        allowed_hosts = {
            f"{cfg_host}:{port}",
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"[::1]:{port}",
        }
        if port == 80:
            allowed_hosts.update({cfg_host, "127.0.0.1", "localhost", "[::1]"})
        host = self.headers.get("Host", "")
        if host not in allowed_hosts:
            self._json(403, {"error": {"message": "Only the local listener Host is accepted.", "type": "invalid_host"}})
            return False
        origin = self.headers.get("Origin")
        sec_fetch_site = self.headers.get("Sec-Fetch-Site")
        if origin is not None or (sec_fetch_site and sec_fetch_site != "none"):
            self._json(403, {"error": {"message": "Browser-origin calls are disabled. Use a local native client.", "type": "browser_origin_denied"}})
            return False
        return True

    def _json(self, status: int, value: Any, **headers: str) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            for key, value_ in headers.items():
                self.send_header(key, value_)
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _proxy(self, method: str, body: bytes | None = None) -> None:
        headers = {
            "Accept": self.headers.get("Accept", "application/json, text/event-stream"),
            "Authorization": f"Bearer {self.profile['api_key']}",
        }
        if body is not None:
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")
        request = Request(_upstream_base(self.profile) + self.path, data=body, method=method, headers=headers)
        try:
            response = urlopen(request, timeout=float(self.profile.get("request_timeout", 120)))
        except HTTPError as exc:
            raw = exc.read()
            try:
                self.send_response(exc.code)
                self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        except URLError as exc:
            self._json(502, {"error": {"message": f"upstream unavailable: {exc.reason}", "type": "upstream_unavailable"}})
            return
        try:
            self.send_response(response.status)
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            self.send_header("Content-Type", content_type)
            if response.headers.get("Cache-Control"):
                self.send_header("Cache-Control", response.headers["Cache-Control"])
            self.send_header("X-ChatGPTProxy-Workspace-Mode", normalize_mode(str(self.profile.get("workspace_mode", "chat_only"))))
            self.end_headers()
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            response.close()

    def do_GET(self) -> None:
        if not self._validate_host_and_origin():
            return
        if self.path == "/health":
            try:
                status, _, raw = _request_upstream(self.profile, "GET", "/health", timeout=5)
                body = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
            except Exception as exc:
                status, body = 502, {"error": str(exc)}
            value = {
                "status": "healthy" if status == 200 else "degraded",
                "gateway": True,
                "workspace_mode": normalize_mode(str(self.profile.get("workspace_mode", "chat_only"))),
                "workspace_path": self.profile.get("workspace_path"),
                "upstream_http_status": status,
                "upstream": body,
            }
            self._json(200 if status == 200 else 503, value)
            return
        if not self._authorized():
            self._json(401, {"error": {"message": "invalid API key", "type": "authentication_error"}})
            return
        self._proxy("GET")

    def do_POST(self) -> None:
        if not self._validate_host_and_origin():
            return
        if not self._authorized():
            self._json(401, {"error": {"message": "invalid API key", "type": "authentication_error"}})
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        mode = normalize_mode(str(self.profile.get("workspace_mode", "chat_only")))
        if self.path != "/v1/chat/completions" or mode == "chat_only":
            self._proxy("POST", body)
            return
        try:
            request_body = json.loads(body.decode("utf-8"))
            if not isinstance(request_body, dict):
                raise ValueError("request body must be a JSON object")
            requested_stream = bool(request_body.get("stream", False))
            payload, tool_steps = _tool_loop(self.profile, request_body)
            if requested_stream and "error" not in payload:
                content = _extract_content(payload)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-ChatGPTProxy-Workspace-Mode", mode)
                    self.send_header("X-ChatGPTProxy-Tool-Steps", str(tool_steps))
                    self.end_headers()
                    for chunk in _sse_chunks(payload, content):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            status = 200 if "error" not in payload else 502
            self._json(status, payload, **{
                "X-ChatGPTProxy-Workspace-Mode": mode,
                "X-ChatGPTProxy-Tool-Steps": str(tool_steps),
            })
        except Exception as exc:
            self._json(500, {"error": {"message": str(exc), "type": "chatgptproxy_workspace_error"}})


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], profile: dict[str, Any]):
        super().__init__(address, GatewayHandler)
        self.profile = profile

    def get_request(self) -> tuple[Any, Any]:
        sock, addr = super().get_request()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
        return sock, addr


def run(profile: dict[str, Any]) -> int:
    host = str(profile.get("host", "127.0.0.1"))
    port = int(profile.get("port", 1235))
    server = GatewayServer((host, port), profile)
    print(f"chatgptproxy gateway listening on http://{host}:{port}/v1", file=sys.stderr)
    print(f"workspace mode: {normalize_mode(str(profile.get('workspace_mode', 'chat_only')))}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m chatgptproxy.gateway")
    parser.add_argument("--profile", default="default")
    args = parser.parse_args()
    try:
        return run(load_profile(args.profile))
    except Exception as exc:
        print(f"gateway error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

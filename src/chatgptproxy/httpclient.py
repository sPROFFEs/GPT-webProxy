from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def json_request(url: str, *, api_key: str | None = None, timeout: float = 3.0) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return exc.code, data
    except URLError as exc:
        raise ConnectionError(str(exc.reason)) from exc

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TOOL_FENCE_RE = re.compile(r"```chatgpt-workspace\s*(.*?)```", re.IGNORECASE | re.DOTALL)

DEFAULT_EXEC_ALLOWLIST = [
    "bash", "sh", "git", "node", "npm", "npx", "pnpm", "yarn", "bun",
    "python", "python3", "pytest", "uv", "pip", "pip3",
    "go", "cargo", "rustc", "make", "cmake", "ninja",
    "gcc", "g++", "clang", "clang++", "java", "javac", "mvn", "gradle",
    "dotnet", "php", "ruby", "bundle", "composer",
    "ruff", "black", "mypy", "eslint", "prettier", "tsc",
]

IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"}


class WorkspaceError(RuntimeError):
    pass


def normalize_mode(value: str | None) -> str:
    raw = (value or "chat_only").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "chat": "chat_only",
        "chat_only": "chat_only",
        "readonly": "read_only",
        "read_only": "read_only",
        "workspace_read_only": "read_only",
        "full": "full_workspace",
        "workspace": "full_workspace",
        "full_workspace": "full_workspace",
    }
    if raw not in aliases:
        raise ValueError("workspace mode must be chat_only, read_only, or full_workspace")
    return aliases[raw]


def mode_label(mode: str) -> str:
    return {
        "chat_only": "CHAT ONLY",
        "read_only": "WORKSPACE READ-ONLY",
        "full_workspace": "FULL WORKSPACE",
    }[normalize_mode(mode)]


def apply_mode(profile: dict[str, Any], mode: str, workspace: str | None = None) -> dict[str, Any]:
    mode = normalize_mode(mode)
    profile["workspace_mode"] = mode
    if mode == "chat_only":
        profile["workspace_path"] = None
        profile["context_policy"] = "none"
        profile["workspace_write_enabled"] = False
        profile["exec_enabled"] = False
    elif mode == "read_only":
        if workspace:
            profile["workspace_path"] = str(Path(workspace).expanduser().resolve())
        profile["context_policy"] = profile.get("context_policy") if profile.get("context_policy") in {"adaptive", "snapshot"} else "adaptive"
        profile["workspace_write_enabled"] = False
        profile["exec_enabled"] = False
    else:
        if workspace:
            profile["workspace_path"] = str(Path(workspace).expanduser().resolve())
        profile["context_policy"] = profile.get("context_policy") if profile.get("context_policy") in {"adaptive", "snapshot"} else "adaptive"
        profile["workspace_write_enabled"] = True
        profile["exec_enabled"] = True
    return profile


def validate_workspace_profile(profile: dict[str, Any]) -> None:
    mode = normalize_mode(str(profile.get("workspace_mode", "chat_only")))
    if mode == "chat_only":
        return
    raw = profile.get("workspace_path")
    if not raw:
        raise WorkspaceError(f"{mode_label(mode)} requires workspace_path")
    root = Path(str(raw)).expanduser()
    if not root.exists():
        raise WorkspaceError(f"workspace does not exist: {root}")
    if not root.is_dir():
        raise WorkspaceError(f"workspace is not a directory: {root}")


class WorkspaceExecutor:
    def __init__(self, profile: dict[str, Any]):
        validate_workspace_profile(profile)
        self.profile = profile
        self.mode = normalize_mode(str(profile.get("workspace_mode", "chat_only")))
        self.root = Path(str(profile.get("workspace_path") or ".")).expanduser().resolve()
        self.max_file_bytes = int(profile.get("workspace_max_file_bytes", 262144))
        self.max_output_bytes = int(profile.get("workspace_max_output_bytes", 65536))
        self.max_actions = int(profile.get("workspace_max_actions_per_step", 8))
        self.include_hidden = bool(profile.get("workspace_include_hidden", False))

    def _safe_path(self, value: str | None, *, must_exist: bool = False) -> Path:
        raw = value or "."
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.expanduser().resolve(strict=False)
        else:
            resolved = (self.root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"path escapes workspace: {raw}") from exc
        if must_exist and not resolved.exists():
            raise WorkspaceError(f"path does not exist: {raw}")
        return resolved

    def _relative(self, path: Path) -> str:
        try:
            value = path.relative_to(self.root)
            return "." if str(value) == "." else str(value)
        except ValueError:
            return str(path)

    def _visible(self, path: Path) -> bool:
        rel = path.relative_to(self.root)
        parts = rel.parts
        if any(part in IGNORED_DIRS for part in parts):
            return False
        if not self.include_hidden and any(part.startswith(".") for part in parts):
            return False
        return True

    def list(self, path: str = ".", *, depth: int = 2, limit: int = 200) -> dict[str, Any]:
        base = self._safe_path(path, must_exist=True)
        if not base.is_dir():
            raise WorkspaceError(f"not a directory: {path}")
        depth = max(0, min(int(depth), 5))
        limit = max(1, min(int(limit), 1000))
        items: list[dict[str, Any]] = []
        base_depth = len(base.parts)
        for item in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
            if not self._visible(item):
                continue
            item_depth = len(item.parts) - base_depth
            if item_depth > depth:
                continue
            try:
                stat = item.stat()
                size = stat.st_size if item.is_file() else None
            except OSError:
                size = None
            items.append({"path": self._relative(item), "type": "dir" if item.is_dir() else "file", "size": size})
            if len(items) >= limit:
                break
        return {"op": "list", "path": self._relative(base), "items": items, "truncated": len(items) >= limit}

    def stat(self, path: str) -> dict[str, Any]:
        target = self._safe_path(path, must_exist=True)
        st = target.stat()
        return {
            "op": "stat",
            "path": self._relative(target),
            "type": "dir" if target.is_dir() else "file",
            "size": st.st_size,
            "mtime": st.st_mtime,
        }

    def read(self, path: str, *, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        target = self._safe_path(path, must_exist=True)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        data = target.read_bytes()[: self.max_file_bytes + 1]
        truncated_bytes = len(data) > self.max_file_bytes
        data = data[: self.max_file_bytes]
        if b"\x00" in data:
            raise WorkspaceError(f"binary file is not supported: {path}")
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(start_line))
        stop = len(lines) if end_line is None else max(start, int(end_line))
        selected = lines[start - 1 : stop]
        return {
            "op": "read",
            "path": self._relative(target),
            "start_line": start,
            "end_line": start + len(selected) - 1 if selected else start,
            "content": "\n".join(selected),
            "truncated": truncated_bytes or stop < len(lines),
        }

    def search(self, query: str, path: str = ".", *, limit: int = 50, case_sensitive: bool = False) -> dict[str, Any]:
        if not query:
            raise WorkspaceError("search query is required")
        base = self._safe_path(path, must_exist=True)
        if not base.is_dir():
            raise WorkspaceError(f"not a directory: {path}")
        limit = max(1, min(int(limit), 200))
        needle = query if case_sensitive else query.lower()
        matches: list[dict[str, Any]] = []
        scanned = 0
        max_files = max(1, int(self.profile.get("workspace_search_max_files", 1000)))
        for file in sorted(base.rglob("*"), key=lambda p: str(p).lower()):
            if len(matches) >= limit or scanned >= max_files:
                break
            if not file.is_file() or not self._visible(file):
                continue
            scanned += 1
            try:
                if file.stat().st_size > self.max_file_bytes:
                    continue
                raw = file.read_bytes()
                if b"\x00" in raw:
                    continue
                text = raw.decode("utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                hay = line if case_sensitive else line.lower()
                if needle in hay:
                    matches.append({"path": self._relative(file), "line": number, "text": line[:500]})
                    if len(matches) >= limit:
                        break
        return {"op": "search", "query": query, "path": self._relative(base), "matches": matches, "scanned_files": scanned, "truncated": len(matches) >= limit or scanned >= max_files}

    def _require_full(self, op: str) -> None:
        if self.mode != "full_workspace":
            raise WorkspaceError(f"operation {op} is not permitted in {mode_label(self.mode)}")

    def write(self, path: str, content: str, *, append: bool = False) -> dict[str, Any]:
        self._require_full("write")
        if not bool(self.profile.get("workspace_write_enabled", True)):
            raise WorkspaceError("workspace writes are disabled by profile")
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        max_write = max(1, int(self.profile.get("workspace_max_write_bytes", 1048576)))
        if len(encoded) > max_write:
            raise WorkspaceError(f"write payload exceeds profile limit: {len(encoded)} > {max_write} bytes")
        if append:
            with target.open("ab") as handle:
                handle.write(encoded)
        else:
            fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                os.replace(tmp_name, target)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
        return {"op": "append" if append else "write", "path": self._relative(target), "bytes": len(encoded)}

    def mkdir(self, path: str) -> dict[str, Any]:
        self._require_full("mkdir")
        target = self._safe_path(path)
        target.mkdir(parents=True, exist_ok=True)
        return {"op": "mkdir", "path": self._relative(target)}

    def delete(self, path: str, *, recursive: bool = False) -> dict[str, Any]:
        self._require_full("delete")
        target = self._safe_path(path, must_exist=True)
        if target == self.root:
            raise WorkspaceError("refusing to delete workspace root")
        if target.is_dir() and not target.is_symlink():
            if not recursive:
                target.rmdir()
            else:
                shutil.rmtree(target)
        else:
            target.unlink()
        return {"op": "delete", "path": path}

    def move(self, source: str, target: str) -> dict[str, Any]:
        self._require_full("move")
        src = self._safe_path(source, must_exist=True)
        dst = self._safe_path(target)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"op": "move", "source": source, "target": target}

    def copy(self, source: str, target: str) -> dict[str, Any]:
        self._require_full("copy")
        src = self._safe_path(source, must_exist=True)
        dst = self._safe_path(target)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return {"op": "copy", "source": source, "target": target}

    def exec(self, argv: list[str], *, cwd: str = ".", timeout_seconds: int | None = None) -> dict[str, Any]:
        self._require_full("exec")
        if not bool(self.profile.get("exec_enabled", True)):
            raise WorkspaceError("local command execution is disabled by profile")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise WorkspaceError("exec argv must be a non-empty string array")
        workdir = self._safe_path(cwd, must_exist=True)
        if not workdir.is_dir():
            raise WorkspaceError(f"exec cwd is not a directory: {cwd}")
        tool_mode = str(self.profile.get("tool_mode", "guarded"))
        executable = Path(argv[0]).name.lower()
        if tool_mode == "guarded":
            allowed = {str(x).lower() for x in self.profile.get("exec_allowlist", DEFAULT_EXEC_ALLOWLIST)}
            if executable not in allowed:
                raise WorkspaceError(f"executable is not in guarded allowlist: {executable}")
        max_timeout = max(1, int(self.profile.get("exec_timeout_seconds", 30)))
        timeout = max(1, min(int(timeout_seconds or max_timeout), max_timeout))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", ""),
            "TERM": os.environ.get("TERM", "dumb"),
            "CI": "1",
        }
        try:
            proc = subprocess.run(
                argv,
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=False,
                timeout=timeout,
                check=False,
            )
            raw = (proc.stdout or b"") + ((b"\n" if proc.stdout and proc.stderr else b"") + (proc.stderr or b""))
            truncated = len(raw) > self.max_output_bytes
            output = raw[: self.max_output_bytes].decode("utf-8", errors="replace")
            return {
                "op": "exec",
                "argv": argv,
                "cwd": self._relative(workdir),
                "exit_code": proc.returncode,
                "output": output,
                "truncated": truncated,
            }
        except subprocess.TimeoutExpired as exc:
            raw = (exc.stdout or b"") + (exc.stderr or b"")
            return {
                "op": "exec",
                "argv": argv,
                "cwd": self._relative(workdir),
                "timed_out": True,
                "timeout_seconds": timeout,
                "output": raw[: self.max_output_bytes].decode("utf-8", errors="replace"),
            }
        except FileNotFoundError as exc:
            raise WorkspaceError(f"executable not found: {argv[0]}") from exc

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict):
            raise WorkspaceError("tool action must be an object")
        op = str(action.get("op") or "").strip().lower()
        if op == "list":
            return self.list(str(action.get("path", ".")), depth=int(action.get("depth", 2)), limit=int(action.get("limit", 200)))
        if op == "stat":
            return self.stat(str(action.get("path", "")))
        if op == "read":
            end = action.get("end_line")
            return self.read(str(action.get("path", "")), start_line=int(action.get("start_line", 1)), end_line=int(end) if end is not None else None)
        if op == "search":
            return self.search(str(action.get("query", "")), str(action.get("path", ".")), limit=int(action.get("limit", 50)), case_sensitive=bool(action.get("case_sensitive", False)))
        if op == "write":
            return self.write(str(action.get("path", "")), str(action.get("content", "")), append=False)
        if op == "append":
            return self.write(str(action.get("path", "")), str(action.get("content", "")), append=True)
        if op == "mkdir":
            return self.mkdir(str(action.get("path", "")))
        if op == "delete":
            return self.delete(str(action.get("path", "")), recursive=bool(action.get("recursive", False)))
        if op == "move":
            return self.move(str(action.get("source", "")), str(action.get("target", "")))
        if op == "copy":
            return self.copy(str(action.get("source", "")), str(action.get("target", "")))
        if op == "exec":
            argv = action.get("argv")
            return self.exec(argv if isinstance(argv, list) else [], cwd=str(action.get("cwd", ".")), timeout_seconds=int(action["timeout_seconds"]) if action.get("timeout_seconds") is not None else None)
        raise WorkspaceError(f"unsupported workspace operation: {op or '<missing>'}")

    def execute_actions(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        if len(actions) > self.max_actions:
            raise WorkspaceError(f"too many actions in one step: {len(actions)} > {self.max_actions}")
        results: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            try:
                result = self.execute_action(action)
                results.append({"index": index, "ok": True, "result": result})
            except Exception as exc:
                results.append({"index": index, "ok": False, "error": str(exc)})
        return {"workspace": str(self.root), "mode": self.mode, "results": results}


def parse_tool_request(content: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    match = TOOL_FENCE_RE.search(content or "")
    if not match:
        return None, None
    raw = match.group(1).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON in chatgpt-workspace block: {exc}"
    actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(actions, list) or not all(isinstance(item, dict) for item in actions):
        return None, "chatgpt-workspace payload must be an object with an actions array"
    return actions, None


def system_prompt(profile: dict[str, Any], executor: WorkspaceExecutor | None = None) -> str:
    mode = normalize_mode(str(profile.get("workspace_mode", "chat_only")))
    if mode == "chat_only":
        return ""
    root = str(profile.get("workspace_path") or "")
    common = f"""You have a local workspace capability supplied by chatgptproxy.\nWorkspace root: {root}\nMode: {mode_label(mode)}\n\nWhen you need real workspace state, respond with a single fenced tool request using exactly this form:\n```chatgpt-workspace\n{{\"actions\":[{{\"op\":\"list\",\"path\":\".\",\"depth\":2}}]}}\n```\nYou may request multiple actions in one actions array. Paths must be relative to the workspace. Do not claim that you inspected, changed, or executed anything unless the tool results confirm it. After tool results are returned, continue the task. Do not show the tool protocol to the end user in the final answer.\n\nAvailable read operations:\n- list: path, depth, limit\n- stat: path\n- read: path, start_line, end_line\n- search: query, path, limit, case_sensitive\n"""
    if mode == "full_workspace":
        common += """\nAvailable FULL WORKSPACE operations:\n- write: path, content\n- append: path, content\n- mkdir: path\n- delete: path, recursive\n- move: source, target\n- copy: source, target\n- exec: argv (string array), cwd, timeout_seconds\n\nPrefer specific file operations over shell commands. Use exec only when execution is materially useful (tests, builds, formatters, package tooling, diagnostics). Commands run without a shell and are bounded by the proxy timeout/output limits.\n"""
    else:
        common += "\nThis is read-only mode. Writing files, deleting/moving content, and executing commands are unavailable.\n"
    if executor and str(profile.get("context_policy", "adaptive")) == "snapshot":
        try:
            snapshot = executor.list(".", depth=2, limit=120)
            common += "\nInitial workspace snapshot:\n" + json.dumps(snapshot, ensure_ascii=False)
        except Exception:
            pass
    return common

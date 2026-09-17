# Changelog

## 0.3.0

- Added public `chatgptproxy` gateway in front of ChatGPT-Web2API.
- Added FULL WORKSPACE, WORKSPACE READ-ONLY and CHAT ONLY product modes.
- Added structured `chatgpt-workspace` tool protocol and bounded tool loop.
- Added workspace root path confinement and symlink-resolved escape rejection.
- Added read/list/stat/search tools.
- Added write/append/mkdir/delete/move/copy tools for FULL WORKSPACE.
- Added bounded `exec` with `shell=False`, reduced environment and guarded executable allowlist.
- Added adaptive and snapshot context policies.
- Added recommended mode configuration to `guided` and the interactive menu.
- Added `configure` and `workspace status/test` commands.
- Split public REST port from the internal ChatGPT-Web2API port.
- Preserved raw upstream SSE pass-through in CHAT ONLY.
- Added final-answer SSE emulation for workspace-mode tool loops.
- Added v0.2 profile migration defaults while retaining API keys/browser state.
- Added gateway/workspace health to status and doctor output.
- Expanded unit/integration tests for workspace and gateway behavior.

## 0.2.0

- Added browser/session supervisor, auth TTL tracking, proactive validation/refresh, auth-required state, and managed browser recovery.

## 0.1.0

- Initial profiles/CLI/lifecycle wrapper around ChatGPT-Web2API.

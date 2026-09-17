# Workspace modes

## FULL WORKSPACE

Capabilities:

- list/stat/read/search files;
- atomic file writes and append;
- create directories;
- move/copy/delete paths;
- execute bounded commands with `shell=False`.

Recommended profile values:

```json
{
  "workspace_mode": "full_workspace",
  "context_policy": "adaptive",
  "workspace_write_enabled": true,
  "exec_enabled": true,
  "tool_mode": "guarded",
  "workspace_max_steps": 6,
  "workspace_max_actions_per_step": 8,
  "exec_timeout_seconds": 30,
  "workspace_max_file_bytes": 262144,
  "workspace_max_write_bytes": 1048576,
  "workspace_max_output_bytes": 65536,
  "workspace_max_tool_feedback_bytes": 131072
}
```

FULL WORKSPACE is an agent execution mode, not a sandbox. Run the proxy inside an OS/container isolation boundary when project code is untrusted.

## WORKSPACE READ-ONLY

Capabilities:

- `list`
- `stat`
- `read`
- `search`

All mutation and execution requests are rejected locally even if the model asks for them.

## CHAT ONLY

No workspace capability prompt is injected and no local tools are available. The gateway acts as a transparent authenticated forwarder to the internal ChatGPT-Web2API instance.

## Tool protocol

A model requests local work using one fenced JSON payload:

````text
```chatgpt-workspace
{"actions":[{"op":"read","path":"README.md"}]}
```
````

`chatgptproxy` validates and executes each action, then feeds a bounded JSON result back into the same request loop.

One malformed request receives a repair turn. The loop has a fixed maximum number of tool iterations and actions per iteration.

## Context policy

`adaptive` (recommended) does not pre-scan the repository. The model asks for files/directories only when needed.

`snapshot` additionally provides a bounded initial directory listing. It can reduce one round trip in small projects but consumes more prompt context on every request.

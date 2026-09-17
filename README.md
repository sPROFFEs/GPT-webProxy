# chatgptproxy — managed ChatGPT Web proxy with local workspace modes

`chatgptproxy` adds an `m365proxy`-style product/control layer around [ChatGPT-Web2API](https://github.com/Octo-Lex/ChatGPT-Web2API): named profiles, isolated browser authentication, TTL/session supervision, a public OpenAI-compatible gateway, recommended configuration menus, and optional local workspace tools.

The ChatGPT Web transport stays in ChatGPT-Web2API. `chatgptproxy` owns the stable local interface and workspace/agent behavior above it.

> [!IMPORTANT]
> OpenAI's consumer terms may restrict automated/programmatic use of ChatGPT Web. Evaluate whether a browser-backed proxy is appropriate for your account/use case. For supported programmatic access, use the official OpenAI API.

## Quick Install (One-Liner)

Install or update `chatgptproxy` in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/sPROFFEs/GPT-webProxy/main/install.sh | bash
```

Then start the interactive setup wizard:

```bash
chatgptproxy guided
```

## v0.3.0 highlights

- **FULL WORKSPACE** — ChatGPT can inspect, create, edit, move, copy, delete, and test files inside one configured workspace, plus run bounded development commands.
- **WORKSPACE READ-ONLY** — real filesystem inspection without writes or local command execution.
- **CHAT ONLY** — no local tools; requests pass through to ChatGPT-Web2API and upstream SSE streaming remains pass-through.
- **Public gateway / internal upstream split** — clients use the stable `chatgptproxy` endpoint while ChatGPT-Web2API runs on a private local port.
- **Structured workspace tool protocol** — no parsing arbitrary shell prose from model output.
- **Root confinement** — workspace file operations reject paths that resolve outside the configured root.
- **Guarded command policy** — FULL WORKSPACE defaults to an explicit development-tool allowlist, timeout, iteration and output limits.
- **Guided recommendations** — menu and `guided` setup explain which mode to choose and apply recommended defaults.
- **Session lifecycle retained** — persistent browser profile, auth TTL tracking, proactive refresh, liveness supervision, `auth_required` handling and browser recovery.

## Architecture

```text
Cursor / Continue / OpenAI SDK / custom client
                    |
                    v
          http://127.0.0.1:1235/v1
                    |
                    v
          +--------------------+
          | chatgptproxy       |
          | public gateway     |
          +---------+----------+
                    |
        +-----------+-----------+
        |                       |
        | workspace modes       | browser/session control
        | tool-loop executor    | profiles / TTL / recovery
        |                       |
        +-----------+-----------+
                    |
                    v
      ChatGPT-Web2API @ 127.0.0.1:13335
                    |
                   CDP
                    |
                  Chrome
                    |
                    v
                ChatGPT Web
```

Default ports:

```text
Public OpenAI-compatible API: 127.0.0.1:1235
Internal ChatGPT-Web2API:      127.0.0.1:13335
MCP SSE:                       127.0.0.1:1236
Chrome CDP:                    127.0.0.1:9232
```

## Operating modes

| Mode | Read files | Write files | Run commands | Streaming behavior | Recommended for |
| --- | --- | --- | --- | --- | --- |
| **FULL WORKSPACE** | Yes | Yes | Yes, bounded | final answer is emitted as compatible SSE after internal tool turns | coding/project work |
| **WORKSPACE READ-ONLY** | Yes | No | No | final answer is emitted as compatible SSE after internal tool turns | code review, analysis, audits |
| **CHAT ONLY** | No | No | No | raw upstream streaming passes through | general ChatGPT use |

### Recommended: FULL WORKSPACE

Use FULL WORKSPACE when the task requires real project state and you want the model to be able to implement/test changes.

Recommended defaults:

```text
context policy:             adaptive
command policy:             guarded
max workspace iterations:   6
max actions per iteration:  8
command timeout:            30 seconds
max command output:         64 KiB
max tool feedback:          128 KiB
max individual file read:   256 KiB
max single write payload:    1 MiB
hidden/vendor dirs:         excluded from discovery
```

The command allowlist is aimed at development tooling (`git`, Node/package managers, Python/test tools, compilers/build tools, formatters, etc.). It reduces accidental command surface but **is not a sandbox**. A permitted interpreter or build tool can still execute code with the privileges of the user running `chatgptproxy`.

### WORKSPACE READ-ONLY

Use this when ChatGPT should inspect the real repository but must not modify it. Available operations are:

```text
list
stat
read
search
```

`write`, `delete`, `move`, `copy`, `mkdir`, and `exec` are rejected by the executor.

### CHAT ONLY

Use this when you only want the ChatGPT Web → OpenAI-compatible bridge. The gateway does not inject a workspace prompt and does not execute local tools.

## Install

### Option A: One-Liner (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/sPROFFEs/GPT-webProxy/main/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
chatgptproxy guided
```

### Option B: Local Checkout

```bash
git clone https://github.com/sPROFFEs/GPT-webProxy.git
cd GPT-webProxy
./install.sh
export PATH="$HOME/.local/bin:$PATH"
chatgptproxy guided
```

### Option C: Pip

```bash
pip install git+https://github.com/sPROFFEs/GPT-webProxy.git
chatgptproxy guided
```

Requirements:

- Python 3.11+
- Chrome/Chromium
- ChatGPT-Web2API 0.2.x
- a ChatGPT account usable by ChatGPT-Web2API

## Recommended menu flow

```bash
chatgptproxy menu
```

The main menu is organized around the active profile:

```text
chatgptproxy 0.3.0
Active profile: default | FULL WORKSPACE
Workspace: /home/user/project

  1. Start profile
  2. Stop profile
  3. Status
  4. Configure operating mode  [recommended]
  5. ChatGPT login / session
  6. Show client configuration
  7. Doctor
  8. Profiles
  9. Advanced
  0. Exit
```

`Configure operating mode` presents the three recommended modes first instead of exposing low-level transport switches.

## Non-interactive configuration

FULL WORKSPACE:

```bash
chatgptproxy configure default \
  --mode full_workspace \
  --workspace /path/to/project \
  --context-policy adaptive
```

READ-ONLY:

```bash
chatgptproxy configure review \
  --mode read_only \
  --workspace /path/to/project
```

CHAT ONLY:

```bash
chatgptproxy configure chat --mode chat_only
```

Inspect configuration:

```bash
chatgptproxy workspace status default
chatgptproxy workspace test default
```

## Start and login

```bash
chatgptproxy start default
```

This starts:

```text
public chatgptproxy gateway
        +
internal ChatGPT-Web2API / Chrome
        +
session supervisor
```

Open/complete ChatGPT login:

```bash
chatgptproxy auth login default
chatgptproxy auth validate default
```

Then verify:

```bash
chatgptproxy status default --check-auth
chatgptproxy probe default
```

## Connect a client

```bash
chatgptproxy endpoint default
```

Default values:

```text
Base URL: http://127.0.0.1:1235/v1
Model:    auto
API key:  output of `chatgptproxy key default`
```

Python example:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:1235/v1",
    api_key="cgp_...",
)

response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Run the tests and fix the failing implementation."}],
)
print(response.choices[0].message.content)
```

In FULL WORKSPACE, the gateway lets ChatGPT inspect the configured workspace and iterate on local results before returning the final answer.

## Workspace tool protocol

The gateway injects a private textual capability contract into workspace-mode requests. When the model needs local state, it requests structured actions:

````text
```chatgpt-workspace
{"actions":[
  {"op":"read","path":"src/app.py","start_line":1,"end_line":200},
  {"op":"search","query":"TODO","path":"src"}
]}
```
````

FULL WORKSPACE additionally supports:

```json
{
  "actions": [
    {"op": "write", "path": "src/new.py", "content": "..."},
    {"op": "exec", "argv": ["pytest", "-q"], "cwd": ".", "timeout_seconds": 30}
  ]
}
```

The model does **not** get a general shell parser. `exec` receives an argument array and is launched with `shell=False`.

Malformed tool blocks receive one repair opportunity. Tool steps are bounded per profile; if the budget is exhausted, the gateway requests a final answer using the verified results already gathered.

## Path confinement

All file operations resolve against the configured workspace root.

The executor rejects traversal such as:

```text
../outside
../../etc/passwd
```

and also rejects paths whose resolved symlink target escapes the workspace.

This is filesystem scope enforcement for the built-in file operations. It is **not an OS sandbox for processes** launched in FULL WORKSPACE.

## Command execution

Guarded FULL WORKSPACE defaults to a development-tool allowlist. Commands:

- run with `shell=False`;
- run with the selected workspace/cwd;
- receive a reduced environment (`PATH`, `HOME`, locale, terminal and `CI=1`);
- have a hard per-command timeout;
- have bounded captured output.

Set `tool_mode=unrestricted` only from Advanced configuration if you intentionally want arbitrary executables. This removes the executable allowlist but does not add sandboxing.

## Context policies

### adaptive (recommended)

The model starts with the workspace capability contract and inspects only what the task needs.

This avoids uploading/scanning the whole repository for every request.

### snapshot

The proxy also injects a bounded two-level initial workspace listing. This can help small projects, at the cost of additional context on every request.

## Browser authentication and TTL lifecycle

Each named profile keeps an isolated persistent Chrome profile:

```text
~/.chatgptproxy/browser/<profile>/
```

The supervisor tracks:

```text
browser/CDP liveness
ChatGPT auth state
access-token expiry/TTL
refresh lead window
upstream breakers
last successful request
consecutive health failures
browser recovery cooldown
```

No ChatGPT access token is stored in `chatgptproxy` session metadata.

Useful commands:

```bash
chatgptproxy auth status default --check
chatgptproxy auth refresh default
chatgptproxy session status default
chatgptproxy session check default
chatgptproxy browser status default
```

`auth_required` is handled separately from browser failure: an expired login does not trigger pointless Chrome restart loops.

## Profiles

```bash
chatgptproxy profile list
chatgptproxy profile show default
chatgptproxy profile clone default project-two
chatgptproxy profile delete project-two
```

A practical setup:

```text
chat       CHAT ONLY
review     WORKSPACE READ-ONLY   /src/customer-project
coding     FULL WORKSPACE        /src/product
```

Each profile has its own:

- browser profile;
- API key;
- mode/workspace;
- ports;
- model configuration;
- execution limits;
- session state/logs.

## Diagnostics

```bash
chatgptproxy status default
chatgptproxy status default --check-auth
chatgptproxy doctor default
chatgptproxy workspace test default
chatgptproxy probe default
```

Logs:

```text
~/.chatgptproxy/logs/default.gateway.log
~/.chatgptproxy/logs/default.upstream.log
~/.chatgptproxy/logs/default.supervisor.log
~/.chatgptproxy/logs/default.mcp.log
```

## Migration from v0.2.x

v0.2 exposed ChatGPT-Web2API directly on the profile's REST port.

v0.3 inserts the workspace-aware gateway on that public port and automatically assigns an internal upstream port to older profiles when `upstream_port` is missing.

Typical migration:

```bash
chatgptproxy stop default
chatgptproxy guided default
chatgptproxy start default
```

The existing browser directory is retained, so a valid logged-in browser profile should remain reusable.

## Streaming notes

- **CHAT ONLY:** upstream SSE is forwarded as it arrives.
- **Workspace modes:** internal tool turns must complete before a final response exists. If the client requested streaming, `chatgptproxy` emits the final answer as OpenAI-compatible SSE after the workspace loop completes.

This prevents intermediate private tool-control messages from leaking to the client.

## Security model

FULL WORKSPACE deliberately gives a model the ability to act on a local project. Treat it accordingly.

Built-in protections include:

- one explicit workspace root;
- path-resolution checks;
- read-only mode;
- atomic file replacement for `write`;
- bounded tool/action iterations;
- command timeout and output limits;
- guarded executable allowlist;
- no shell expansion for `exec`;
- reduced subprocess environment;
- excluded hidden/vendor directories during automatic discovery.

These protections reduce accidental scope and damage. **They do not provide process isolation.** A permitted compiler, interpreter, package manager, build script, or project test can execute arbitrary code with the account privileges of the `chatgptproxy` process.

Use READ-ONLY when execution is not required. For stronger isolation, run FULL WORKSPACE inside a container/VM with only the intended project mounted.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The test suite covers profile/session behavior plus workspace mode policy, root-escape rejection, writes, guarded execution, tool-protocol parsing and gateway SSE helpers.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Session lifecycle](docs/SESSION_LIFECYCLE.md)
- [Workspace modes](docs/WORKSPACE_MODES.md)
- [Implementation notes](IMPLEMENTATION_NOTES.md)
- [Third-party software](THIRD_PARTY.md)

## License

MIT. See [LICENSE](LICENSE).

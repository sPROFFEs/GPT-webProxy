# Architecture

## Components

`chatgptproxy` v0.3 has four managed layers:

1. **Public gateway** — stable OpenAI-compatible endpoint used by clients.
2. **Workspace engine** — optional local tool loop for READ-ONLY/FULL WORKSPACE profiles.
3. **ChatGPT-Web2API** — internal ChatGPT Web REST/browser transport.
4. **Session supervisor** — browser/auth TTL monitoring and upstream recovery.

```text
client
  |
  v
chatgptproxy gateway :1235
  |             |
  |             +--> WorkspaceExecutor -> local project
  |
  v
ChatGPT-Web2API :13335
  |
 CDP :9232
  |
Chrome -> chatgpt.com
```

The public and upstream ports are separate so the local product layer can add workspace behavior without forking ChatGPT-Web2API.

## Request routing

### CHAT ONLY

```text
client request -> gateway -> upstream request -> client response
```

The proxy streams upstream response bytes directly to the client.

### Workspace modes

```text
client request
   |
   +--> inject private workspace capability contract
   |
   v
upstream non-streaming model turn
   |
   +--> final answer -----------------------------> client
   |
   +--> chatgpt-workspace request
            |
            v
      WorkspaceExecutor
            |
      bounded local result
            |
            +--> model feedback turn -> repeat
```

Intermediate tool-control messages never become client-visible assistant output.

## Workspace isolation boundary

Built-in filesystem operations resolve requested paths to an absolute path and require that path to remain beneath the configured workspace root.

READ-ONLY registers only inspection operations. FULL WORKSPACE registers mutations and command execution.

Command execution is a different trust boundary: it runs a real local process. Guarded mode limits executable names and execution duration/output, but it is not a sandbox.

## Lifecycle ownership

ChatGPT-Web2API continues to own Chrome. The `chatgptproxy` session supervisor observes/reconciles it. The gateway is independent and can survive an upstream browser restart.

Managed PIDs:

```text
<profile>.gateway.pid
<profile>.pid              # ChatGPT-Web2API
<profile>.mcp.pid
<profile>.supervisor.pid
```

## Profile migration

`load_profile()` overlays saved data on v0.3 defaults. Profiles created before v0.3 that do not have `upstream_port` are given a deterministic internal port derived from the old public port.

# Session lifecycle

`chatgptproxy` introduced its session supervisor in v0.2.0 and retains it in v0.3.0 behind the new public gateway. It does not send synthetic chat messages to keep a session alive and it does not persist ChatGPT access tokens.

## Signals

The supervisor treats three health domains separately:

1. **REST/process health** — whether the managed ChatGPT-Web2API process is alive and `/health` responds.
2. **Browser health** — whether Chrome is running, CDP is reachable, and the upstream driver is connected.
3. **Authentication health** — whether the logged-in browser can fetch `/api/auth/session`, plus the access-token expiry reported by that session.

This distinction is deliberate. Restarting Chrome can repair a dead browser, but it cannot repair an expired ChatGPT login.

## State file

Each named profile gets a user-only runtime state file:

```text
~/.chatgptproxy/runtime/<profile>.session.json
```

The file stores operational metadata only, for example:

```json
{
  "auth_state": "READY",
  "browser_state": "READY",
  "needs_login": false,
  "token_expires_at": "2026-09-16T23:55:00Z",
  "token_ttl_seconds": 2670,
  "last_auth_check_at": "2026-09-16T23:10:30Z",
  "last_auth_refresh_at": "2026-09-16T23:10:30Z",
  "last_successful_send_at": null,
  "consecutive_auth_failures": 0,
  "consecutive_health_failures": 0
}
```

It intentionally does **not** persist the ChatGPT `accessToken`, browser cookies, or account email. Browser cookies remain inside the isolated Chrome user-data directory managed by the upstream service.

## Authentication states

- `UNKNOWN` — no successful validation has been recorded yet.
- `READY` — browser session is authenticated and token TTL is outside the refresh window.
- `REFRESH_SOON` — the token is valid but its TTL is inside `auth_refresh_lead_seconds`.
- `NEEDS_LOGIN` — the browser session cannot produce a valid authenticated session, or the upstream `auth_required` breaker is open and recovery failed.
- `CHECK_FAILED` — the browser exists but the session probe itself failed.
- `EXPIRED` — an expiry was returned and is already in the past.

## Default timings

Profiles default to:

```text
health_check_interval_seconds       30
auth_check_interval_seconds        300
auth_refresh_lead_seconds          600
health_failure_restart_threshold     3
broken_restart_threshold             2
browser_restart_cooldown_seconds   120
```

These values live in the profile JSON and can be changed there or through `chatgptproxy guided` for the main session settings.

## Auth validation and refresh

The managed ChatGPT browser is inspected over its existing CDP connection. `chatgptproxy` executes a same-origin browser fetch for:

```text
GET /api/auth/session
```

The response is used only to determine whether the browser is authenticated and to obtain expiry metadata. The access token is discarded immediately after extracting expiry information.

Commands:

```bash
chatgptproxy auth status default
chatgptproxy auth validate default
chatgptproxy auth refresh default
chatgptproxy auth login default
```

`auth login` opens `https://chatgpt.com/` in the managed browser. After completing interactive login, run:

```bash
chatgptproxy auth validate default
```

If ChatGPT-Web2API still has an `auth_required` breaker after the browser login is valid, `auth refresh` invokes the upstream `ensure` path so upstream can run its own `recover_auth()` logic and reset the breaker.

## Browser recovery policy

Automatic recovery follows these rules:

```text
auth_required / NEEDS_LOGIN
        -> never restart Chrome as an auth fix

status=degraded
        -> observe; do not immediately restart

managed REST process exited
        -> restart REST/Chrome

persistent health failure
        -> restart after threshold and cooldown

persistent status=broken
        -> restart after threshold and cooldown
```

When a restart is required, MCP is stopped first, REST/Chrome is restarted, browser/CDP readiness is awaited, then MCP is reattached. The session supervisor itself remains alive.

Manual browser recovery:

```bash
chatgptproxy browser status default
chatgptproxy browser restart default
```

`browser restart` is intentionally blocked when upstream reports `auth_required`, because a browser restart cannot repair an expired login.

## Supervisor lifecycle

`chatgptproxy start` starts the supervisor by default:

```bash
chatgptproxy start default
```

Disable it explicitly with:

```bash
chatgptproxy start default --no-supervisor
```

Foreground or one-shot operation is also available:

```bash
chatgptproxy session keepalive default
chatgptproxy session keepalive default --once
```

The supervisor log is:

```text
~/.chatgptproxy/logs/<profile>.supervisor.log
```

`chatgptproxy stop` terminates the supervisor first, preventing it from racing an intentional shutdown and restarting the REST service.

## Status

Use:

```bash
chatgptproxy status default
```

For a live auth validation before printing status:

```bash
chatgptproxy status default --check-auth
```

Machine-readable status:

```bash
chatgptproxy status default --json
```

## No synthetic traffic

The keepalive mechanism does not create conversations or send prompts. It uses health endpoints, CDP liveness, and the browser authentication-session endpoint. Real chat requests remain the only source of conversation traffic.

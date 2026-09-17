# Implementation notes — v0.3.0

## Goal

Bring the product behavior of the existing M365-style wrapper to ChatGPT-Web2API without copying or forking its browser transport.

## Design decision: gateway instead of upstream patching

ChatGPT-Web2API remains an internal service. `chatgptproxy.gateway` owns the client-facing port and can therefore implement profiles/workspace policy independently of upstream browser changes.

This also keeps browser auth/session lifecycle separate from local workspace execution.

## FULL WORKSPACE contract

The model never sends raw shell script blocks for the proxy to blindly execute. It sends structured JSON actions in a `chatgpt-workspace` fence.

The executor has explicit operations and validates all filesystem paths.

`exec` accepts an argv list and uses `subprocess.run(..., shell=False)` with timeout/output limits. Guarded mode checks the executable basename against a profile allowlist.

## Streaming

CHAT ONLY uses byte-streaming pass-through.

Workspace modes need complete internal turns so tool requests can be parsed and hidden from clients. The final response is therefore converted back to OpenAI-compatible SSE when the original request used `stream=true`.

## Session/auth

The v0.2 session supervisor remains authoritative for browser health and auth TTL. It queries the internal upstream `/health` endpoint after the v0.3 port split. Browser recovery restarts ChatGPT-Web2API while the gateway can stay alive.

## Compatibility

Saved v0.2 profiles are merged with v0.3 defaults at load time. Existing API keys/browser directories are retained.

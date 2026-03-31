# Remote AIO Sandbox Minimal Plan

## Goal

Keep AIO Sandbox as a long-lived remote service that we do not deploy ourselves, and make the existing RPA flow work with the smallest practical set of changes.

Scope for this phase:

1. Backend can drive the remote browser through the remote AIO Sandbox MCP endpoint.
2. Frontend can display the remote browser page through a remote VNC URL.
3. Do not introduce sandbox instance lifecycle management.
4. Do not refactor the whole DeepAgent sandbox stack yet.

## Current Constraints

The current project assumes a locally deployed sandbox container:

1. Backend RPA code sends MCP requests to a fixed local sandbox URL.
2. Frontend recorder page builds VNC URLs from local ports like `18080`.
3. Authentication for a hosted sandbox is not handled consistently.

## Minimal Change Plan

### 1. Centralize remote sandbox config

Add explicit backend settings for:

1. `SANDBOX_BASE_URL`
2. `SANDBOX_MCP_URL`
3. `SANDBOX_API_TOKEN`
4. `SANDBOX_VNC_URL`

Rules:

1. `SANDBOX_BASE_URL` points to the hosted AIO Sandbox origin.
2. `SANDBOX_MCP_URL` falls back to `SANDBOX_BASE_URL + "/mcp"`.
3. `SANDBOX_VNC_URL` can be set directly for hosted environments that need a custom ticket/query string.

### 2. Add a small sandbox request helper

Create a shared helper that:

1. Builds authenticated request headers for sandbox REST/MCP calls.
2. Adds `Authorization: Bearer ...` when `SANDBOX_API_TOKEN` is configured.
3. Resolves the browser preview URL from `SANDBOX_VNC_URL` or `SANDBOX_BASE_URL`.

### 3. Patch only the RPA path first

Update only these backend modules in this phase:

1. `backend/rpa/manager.py`
2. `backend/rpa/executor.py`
3. `backend/rpa/assistant.py`
4. `backend/route/rpa.py`

Behavior:

1. MCP requests use the configured remote AIO Sandbox URL.
2. MCP requests include auth headers when configured.
3. `POST /api/v1/rpa/session/start` returns the resolved VNC URL for the recorder page.

### 4. Make recorder UI consume a remote VNC URL

Update the recorder page to:

1. Stop hardcoding local sandbox ports for the main recording viewport.
2. Read the VNC proxy URL returned by the backend.
3. Render that URL in the existing `iframe`.

The browser should no longer connect to the hosted sandbox origin directly. Instead:

1. Frontend loads the iframe from the backend.
2. Backend proxies VNC HTML/assets/websocket traffic to the hosted sandbox.
3. Backend injects sandbox auth on those proxied requests.

### 5. Leave the rest untouched in this phase

Do not refactor yet:

1. `backend/deepagent/full_sandbox_backend.py`
2. `Tools/__init__.py`
3. `frontend/src/components/VNCViewer.vue`

Those can be migrated later if we decide to move the whole app, not just RPA, onto the hosted sandbox.

## Environment Variables

Backend:

1. `SANDBOX_BASE_URL=https://your-hosted-sandbox`
2. `SANDBOX_MCP_URL=https://your-hosted-sandbox/mcp`
3. `SANDBOX_API_TOKEN=...`
4. `SANDBOX_VNC_URL=https://your-hosted-sandbox/vnc/index.html?autoconnect=true&resize=scale&view_only=false`
5. `SANDBOX_EXTRA_HEADERS={"X-Your-Header":"value","Cookie":"..."}`

Frontend:

1. No new frontend-only variable is required for the recorder page after this change.
2. The page should consume the VNC URL returned by the backend.

Notes:

1. `SANDBOX_EXTRA_HEADERS` is a JSON object string.
2. These headers are attached by the backend when it calls sandbox HTTP endpoints and when it opens the websocket to sandbox.

## Validation Checklist

1. Start an RPA session successfully.
2. Recorder page displays the hosted browser in the iframe.
3. Backend can call remote MCP tools with auth enabled.
4. AI assistant can still read page state and execute Playwright snippets.

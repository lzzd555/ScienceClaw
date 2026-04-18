# MCP Multi-Credential Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a user MCP server to bind multiple saved credentials and inject them into MCP headers, env vars, and query parameters at runtime.

**Architecture:** Store credential bindings as aliases plus template maps under `credential_binding`, keep direct `endpoint_config.headers/env` as static configuration, and resolve templates only in backend memory before runtime creation. The frontend edits aliases and template maps; plaintext secrets never leave the credential vault.

**Tech Stack:** FastAPI, Pydantic v2, MongoDB repositories, AES-GCM credential vault, Vue 3, TypeScript, Vitest, pytest.

---

### Task 1: Backend Model And Resolver

**Files:**
- Modify: `RpaClaw/backend/mcp/models.py`
- Create: `RpaClaw/backend/deepagent/mcp_credentials.py`
- Test: `RpaClaw/backend/tests/deepagent/test_mcp_credentials.py`

- [ ] Add tests for resolving two credential aliases into headers, env, and query templates.
- [ ] Add tests for legacy `credential_id` resolving through the `credential` alias.
- [ ] Add tests for unknown aliases or missing credentials raising a clear error.
- [ ] Implement `McpCredentialRef` and extend `McpCredentialBinding` with `credentials`.
- [ ] Implement `resolve_mcp_credentials(user_id, binding, vault)` that returns resolved `headers`, `env`, and `query` maps.

### Task 2: Runtime Injection

**Files:**
- Modify: `RpaClaw/backend/deepagent/mcp_registry.py`
- Modify: `RpaClaw/backend/route/mcp.py`
- Test: `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`
- Test: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] Add route test proving `credential_binding.credentials` is accepted and stored.
- [ ] Add registry test proving resolved credentials override static endpoint values in the effective server.
- [ ] Inject resolved headers/env/query while building effective MCP servers for sessions.
- [ ] Inject resolved credentials for route-level `test` and `discover-tools`.
- [ ] Append resolved query params to `streamable_http` and `sse` URLs.

### Task 3: Frontend Configuration

**Files:**
- Modify: `RpaClaw/frontend/src/api/mcp.ts`
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
- Modify: `RpaClaw/frontend/src/utils/mcpUi.ts`
- Modify: `RpaClaw/frontend/src/utils/mcpUi.test.ts`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] Add TypeScript types for `credential_binding.credentials`.
- [ ] Add UI rows for alias plus credential selection.
- [ ] Add header/env/query template textareas.
- [ ] Preserve the existing direct HTTP headers textarea as static `endpoint_config.headers`.
- [ ] Add Vitest coverage for parsing/stringifying key-value template maps.

### Task 4: Verification

**Commands:**
- `python -m pytest backend/tests/deepagent/test_mcp_credentials.py backend/tests/deepagent/test_mcp_registry.py backend/tests/test_mcp_route.py -q --basetemp .pytest_tmp_mcp`
- `npm test -- src/utils/mcpUi.test.ts`
- `npm run build`

- [ ] Run backend tests and confirm all selected tests pass.
- [ ] Run frontend tests and confirm selected tests pass.
- [ ] Run frontend build and confirm exit code 0.

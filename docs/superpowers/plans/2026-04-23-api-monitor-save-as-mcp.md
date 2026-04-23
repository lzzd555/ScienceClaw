# API Monitor Save-As-MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace API Monitor's YAML export and per-tool save flow with a batch "save as MCP" flow that publishes one user MCP containing one tool per extracted API.

**Architecture:** Keep `api-monitor` session data as the temporary editing surface, then add a dedicated publish endpoint that reads the session's current tool set and persists it as a user MCP plus child API tools. Extend the existing MCP list/discovery flow so `source_type="api_monitor"` servers appear in "My MCP" and discover their tools from internal storage instead of an external endpoint.

**Tech Stack:** FastAPI, Pydantic v2, Vue 3 + TypeScript, existing `get_repository(...)` storage abstraction, pytest, Vitest

---

## File Map

### Backend

- Modify: `RpaClaw/backend/storage/__init__.py`
  - Register the new `api_monitor_mcp_tools` collection for local file-backed mode startup.
- Modify: `RpaClaw/backend/mcp/models.py`
  - Extend MCP payload models so user MCP docs can carry an internal source marker without pretending to be only remote transports.
- Modify: `RpaClaw/backend/route/mcp.py`
  - Serialize `api_monitor` MCPs into the existing MCP listing API.
  - Discover tools for `api_monitor` MCPs from internal storage.
  - Delete child tools when deleting an `api_monitor` MCP.
- Modify: `RpaClaw/backend/route/api_monitor.py`
  - Remove `/export`, add publish endpoints for create/overwrite-confirm.
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
  - Add publish request/response models if kept with API Monitor route models.
- Create: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
  - Encapsulate CRUD for MCP child API tools and overwrite semantics.
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
  - New route-level tests for publish flow, duplicate detection, overwrite, and local-storage-compatible persistence behavior via repository abstraction.
- Modify: `RpaClaw/backend/tests/test_mcp_route.py`
  - Cover MCP listing/discovery/deletion behavior for `source_type="api_monitor"`.

### Frontend

- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
  - Remove export client, add publish/overwrite-confirm request helpers and related types.
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - Remove per-tool save button behavior.
  - Replace export button with save-as-MCP flow.
  - Add save dialog, duplicate-confirm dialog, success handling.
- Test: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts`
  - New focused UI tests for publish behavior.
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
  - Render source hint for API Monitor MCP entries and ensure tools dialog can fetch internal tools through existing discovery API.
- Test: `RpaClaw/frontend/src/utils/mcpUi.test.ts`
  - Verify API Monitor MCP items are rendered coherently in My MCP.

## Task 1: Define the persisted API Monitor MCP shape

**Files:**
- Modify: `RpaClaw/backend/mcp/models.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Create: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Write the failing backend model/registry tests**

```python
from backend.rpa.api_monitor_mcp_registry import ApiMonitorMcpRegistry


async def test_registry_replace_tools_rewrites_existing_collection(fake_repo_pair):
    server_repo, tool_repo = fake_repo_pair
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await registry.replace_tools(
        mcp_server_id="mcp_existing",
        user_id="u1",
        session_tools=[
            {
                "id": "tool_1",
                "name": "list_users",
                "description": "List users",
                "method": "GET",
                "url_pattern": "/api/users",
                "yaml_definition": "name: list_users",
            }
        ],
    )

    tools = await tool_repo.find_many({"mcp_server_id": "mcp_existing", "user_id": "u1"})
    assert len(tools) == 1
    assert tools[0]["name"] == "list_users"
    assert tools[0]["source"] == "api_monitor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k replace_tools -v`

Expected: FAIL with import error for `ApiMonitorMcpRegistry` or missing method assertions.

- [ ] **Step 3: Add the minimal persistence models and registry**

```python
# RpaClaw/backend/rpa/api_monitor_mcp_registry.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.storage import get_repository


class ApiMonitorMcpRegistry:
    def __init__(self, server_repository=None, tool_repository=None) -> None:
        self._servers = server_repository or get_repository("user_mcp_servers")
        self._tools = tool_repository or get_repository("api_monitor_mcp_tools")

    async def replace_tools(self, *, mcp_server_id: str, user_id: str, session_tools: list[dict[str, Any]]) -> None:
        await self._tools.delete_many({"mcp_server_id": mcp_server_id, "user_id": user_id})
        now = datetime.now()
        for tool in session_tools:
            await self._tools.insert_one(
                {
                    "_id": tool.get("id") or f"{mcp_server_id}_{tool['name']}",
                    "user_id": user_id,
                    "mcp_server_id": mcp_server_id,
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "method": tool["method"],
                    "url_pattern": tool["url_pattern"],
                    "headers_schema": tool.get("headers_schema"),
                    "request_body_schema": tool.get("request_body_schema"),
                    "response_body_schema": tool.get("response_body_schema"),
                    "yaml_definition": tool.get("yaml_definition", ""),
                    "source": "api_monitor",
                    "created_at": now,
                    "updated_at": now,
                }
            )
```

```python
# RpaClaw/backend/storage/__init__.py
for name in (
    "users", "user_sessions", "sessions", "models",
    "skills", "blocked_tools", "task_settings", "session_events",
    "session_runtimes", "credentials", "rpa_mcp_tools",
    "api_monitor_mcp_tools",
):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k replace_tools -v`

Expected: PASS with one inserted tool carrying `source="api_monitor"`.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/storage/__init__.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: add api monitor mcp registry"
```

## Task 2: Add the API Monitor publish endpoint

**Files:**
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Write the failing publish route tests**

```python
def test_publish_api_monitor_mcp_requires_confirm_on_duplicate(client, seeded_duplicate_server):
    response = client.post(
        "/api/v1/api-monitor/session/session_1/publish-mcp",
        json={"mcp_name": "Example MCP", "description": "Captured APIs", "confirm_overwrite": False},
        headers=_auth_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "MCP with the same name already exists"
    assert response.json()["needs_confirmation"] is True


def test_publish_api_monitor_mcp_creates_server_and_tools(client, seeded_api_monitor_session):
    response = client.post(
        f"/api/v1/api-monitor/session/{seeded_api_monitor_session.id}/publish-mcp",
        json={"mcp_name": "Example MCP", "description": "Captured APIs", "confirm_overwrite": False},
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["saved"] is True
    assert data["server_id"].startswith("mcp_")
    assert data["tool_count"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k publish_api_monitor_mcp -v`

Expected: FAIL with `404` or missing route/model definitions.

- [ ] **Step 3: Add publish request/response models and route**

```python
# RpaClaw/backend/rpa/api_monitor/models.py
class PublishMcpRequest(BaseModel):
    mcp_name: str
    description: str = ""
    confirm_overwrite: bool = False


class PublishMcpResponse(BaseModel):
    saved: bool
    server_id: str
    tool_count: int
    overwritten: bool = False
```

```python
# RpaClaw/backend/route/api_monitor.py
@router.post("/session/{session_id}/publish-mcp")
async def publish_mcp(
    session_id: str,
    request: PublishMcpRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    existing = await get_repository("user_mcp_servers").find_one(
        {
            "user_id": str(current_user.id),
            "name": request.mcp_name,
            "source_type": "api_monitor",
        }
    )
    if existing and not request.confirm_overwrite:
        raise HTTPException(
            status_code=409,
            detail="MCP with the same name already exists",
            headers={"X-Needs-Confirmation": "true"},
        )

    registry = ApiMonitorMcpRegistry()
    result = await registry.publish_session(
        session=session,
        user_id=str(current_user.id),
        mcp_name=request.mcp_name,
        description=request.description,
        overwrite=bool(existing),
        existing_server_id=str(existing["_id"]) if existing else None,
    )
    return {"status": "success", "data": result}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k publish_api_monitor_mcp -v`

Expected: PASS for both create and duplicate-confirm tests.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: add api monitor publish endpoint"
```

## Task 3: Implement overwrite semantics in the registry

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Write the failing overwrite test**

```python
async def test_publish_session_overwrite_replaces_all_existing_tools(fake_repo_pair, fake_session):
    server_repo, tool_repo = fake_repo_pair
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await server_repo.insert_one(
        {
            "_id": "mcp_existing",
            "user_id": "u1",
            "name": "Example MCP",
            "description": "Old",
            "transport": "api_monitor",
            "source_type": "api_monitor",
        }
    )
    await tool_repo.insert_one(
        {
            "_id": "old_tool",
            "user_id": "u1",
            "mcp_server_id": "mcp_existing",
            "name": "old_tool",
            "method": "GET",
            "url_pattern": "/old",
            "source": "api_monitor",
        }
    )

    result = await registry.publish_session(
        session=fake_session,
        user_id="u1",
        mcp_name="Example MCP",
        description="New description",
        overwrite=True,
        existing_server_id="mcp_existing",
    )

    tools = await tool_repo.find_many({"mcp_server_id": "mcp_existing", "user_id": "u1"})
    assert result["overwritten"] is True
    assert [tool["name"] for tool in tools] == ["list_users", "create_user"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k overwrite_replaces_all_existing_tools -v`

Expected: FAIL because `publish_session` does not yet update the MCP doc and fully replace child tools.

- [ ] **Step 3: Implement `publish_session` with full replacement**

```python
# RpaClaw/backend/rpa/api_monitor_mcp_registry.py
async def publish_session(
    self,
    *,
    session,
    user_id: str,
    mcp_name: str,
    description: str,
    overwrite: bool,
    existing_server_id: str | None,
) -> dict[str, Any]:
    now = datetime.now()
    server_id = existing_server_id or f"mcp_{uuid.uuid4().hex[:12]}"
    await self._servers.update_one(
        {"_id": server_id, "user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "name": mcp_name,
                "description": description,
                "transport": "api_monitor",
                "enabled": True,
                "default_enabled": False,
                "source_type": "api_monitor",
                "tool_count": len(session.tool_definitions),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    await self.replace_tools(
        mcp_server_id=server_id,
        user_id=user_id,
        session_tools=[tool.model_dump(mode="python") for tool in session.tool_definitions],
    )
    return {
        "saved": True,
        "server_id": server_id,
        "tool_count": len(session.tool_definitions),
        "overwritten": overwrite,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -k overwrite_replaces_all_existing_tools -v`

Expected: PASS with the old child tool removed and only the current session tools present.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: support api monitor mcp overwrite"
```

## Task 4: Remove export and per-tool save from the frontend API surface

**Files:**
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Test: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts`

- [ ] **Step 1: Write the failing frontend API test**

```ts
it('posts publish requests to the api monitor publish endpoint', async () => {
  vi.spyOn(apiClient, 'post').mockResolvedValue({
    data: { data: { saved: true, server_id: 'mcp_123', tool_count: 2, overwritten: false } },
  } as any)

  const result = await publishMcpToolBundle('session_1', {
    mcp_name: 'Example MCP',
    description: 'Captured APIs',
    confirm_overwrite: false,
  })

  expect(apiClient.post).toHaveBeenCalledWith('/api-monitor/session/session_1/publish-mcp', {
    mcp_name: 'Example MCP',
    description: 'Captured APIs',
    confirm_overwrite: false,
  })
  expect(result.saved).toBe(true)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- ApiMonitorPage.test.ts`

Expected: FAIL because `publishMcpToolBundle` does not exist.

- [ ] **Step 3: Replace export helper with publish helper**

```ts
export interface PublishMcpPayload {
  mcp_name: string
  description: string
  confirm_overwrite: boolean
}

export interface PublishMcpResult {
  saved: boolean
  server_id: string
  tool_count: number
  overwritten: boolean
}

export async function publishMcpToolBundle(
  sessionId: string,
  payload: PublishMcpPayload,
): Promise<PublishMcpResult> {
  const response = await apiClient.post(`/api-monitor/session/${sessionId}/publish-mcp`, payload)
  return response.data.data
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- ApiMonitorPage.test.ts`

Expected: PASS with the publish API helper calling the new endpoint.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts
git commit -m "refactor: replace api monitor export api with publish api"
```

## Task 5: Replace the page-level export flow with save-as-MCP dialogs

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
- Test: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts`

- [ ] **Step 1: Write the failing UI tests**

```ts
it('hides single-tool save controls and keeps delete/edit controls', async () => {
  render(ApiMonitorPage, { /* seeded tools */ })

  expect(screen.queryByText('Save Tool')).not.toBeInTheDocument()
  expect(screen.getByText('Delete')).toBeInTheDocument()
})

it('opens overwrite confirmation when publish returns duplicate conflict', async () => {
  mockedPublish.mockRejectedValue({
    response: { status: 409, data: { detail: 'MCP with the same name already exists', needs_confirmation: true } },
  })

  render(ApiMonitorPage, { /* seeded tools */ })
  await user.click(screen.getByRole('button', { name: 'Save as MCP Tool' }))
  await user.type(screen.getByLabelText('MCP Name'), 'Example MCP')
  await user.click(screen.getByRole('button', { name: 'Save' }))

  expect(await screen.findByText('Replace existing MCP tools?')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- ApiMonitorPage.test.ts`

Expected: FAIL because the page still renders export/per-tool save behavior.

- [ ] **Step 3: Implement the dialog-based save flow**

```ts
const publishDialogOpen = ref(false)
const overwriteDialogOpen = ref(false)
const publishForm = reactive({
  mcpName: '',
  description: '',
})

const openPublishDialog = () => {
  publishForm.mcpName = session.value?.target_url ? new URL(session.value.target_url).hostname : 'API Monitor MCP'
  publishForm.description = session.value?.target_url || ''
  publishDialogOpen.value = true
}

const submitPublish = async (confirmOverwrite = false) => {
  const result = await publishMcpToolBundle(sessionId.value, {
    mcp_name: publishForm.mcpName.trim(),
    description: publishForm.description.trim(),
    confirm_overwrite: confirmOverwrite,
  })
  publishDialogOpen.value = false
  overwriteDialogOpen.value = false
  addLog('INFO', `Saved MCP ${publishForm.mcpName} with ${result.tool_count} tools`)
}
```

```vue
<button
  @click="openPublishDialog"
  :disabled="!sessionId || !tools.length"
  class="..."
>
  Save as MCP Tool
</button>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- ApiMonitorPage.test.ts`

Expected: PASS with publish dialog, duplicate-confirm path, and no single-tool save button.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts
git commit -m "feat: add api monitor save as mcp flow"
```

## Task 6: Surface API Monitor MCPs through the existing MCP list/discovery APIs

**Files:**
- Modify: `RpaClaw/backend/route/mcp.py`
- Modify: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: Write the failing MCP route tests**

```python
def test_list_mcp_servers_includes_api_monitor_source(client, monkeypatch):
    async def fake_user_servers(user_id: str):
        return [
            {
                "_id": "mcp_api_monitor",
                "name": "Example MCP",
                "description": "Captured APIs",
                "transport": "api_monitor",
                "enabled": True,
                "default_enabled": False,
                "source_type": "api_monitor",
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    response = client.get("/api/v1/mcp/servers", headers=_auth_headers())

    assert response.status_code == 200
    server = response.json()["data"][0]
    assert server["transport"] == "api_monitor"
    assert server["scope"] == "user"


def test_discover_mcp_tools_reads_internal_api_monitor_tools(client, monkeypatch):
    async def fake_resolve(server_key: str, user_id: str):
        return {
            "id": "mcp_api_monitor",
            "server_key": "user:mcp_api_monitor",
            "scope": "user",
            "name": "Example MCP",
            "transport": "api_monitor",
            "source_type": "api_monitor",
        }

    monkeypatch.setattr(mcp_route, "_resolve_server_by_key", fake_resolve)
    monkeypatch.setattr(mcp_route, "_load_api_monitor_tools", lambda server_id, user_id: [
        {"name": "list_users", "description": "List users", "input_schema": {"type": "object"}}
    ])

    response = client.post("/api/v1/mcp/servers/user%3Amcp_api_monitor/discover-tools", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json()["data"]["tool_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_mcp_route.py -k api_monitor -v`

Expected: FAIL because `api_monitor` transport is not yet serialized/discovered.

- [ ] **Step 3: Add serialization/discovery support**

```python
def _serialize_user_server(doc: Dict[str, Any]) -> Dict[str, Any]:
    endpoint_config = doc.get("endpoint_config") or {}
    transport = doc.get("transport", "streamable_http")
    if doc.get("source_type") == "api_monitor":
        endpoint_config = {}
        transport = "api_monitor"
    return McpServerListItem(
        id=str(doc["_id"]),
        server_key=f"user:{doc['_id']}",
        scope="user",
        name=doc["name"],
        description=doc.get("description", ""),
        transport=transport,
        enabled=doc.get("enabled", True),
        default_enabled=doc.get("default_enabled", False),
        readonly=False,
        endpoint_config=endpoint_config,
        credential_binding=doc.get("credential_binding") or {},
        tool_policy=doc.get("tool_policy") or {},
    ).model_dump()
```

```python
async def _discover_tools(server_key: str, user_id: str) -> Dict[str, Any]:
    server = await _resolve_server_by_key(server_key, user_id)
    if server.get("source_type") == "api_monitor":
        raw_tools = await _load_api_monitor_tools(server["id"], user_id)
        return {
            "server_key": server_key,
            "tools": raw_tools,
            "tool_count": len(raw_tools),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_mcp_route.py -k api_monitor -v`

Expected: PASS with API Monitor MCP entries returned and internal tools discoverable.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/route/mcp.py RpaClaw/backend/tests/test_mcp_route.py
git commit -m "feat: expose api monitor mcps in mcp routes"
```

## Task 7: Keep deletion and My MCP rendering consistent

**Files:**
- Modify: `RpaClaw/backend/route/mcp.py`
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
- Modify: `RpaClaw/frontend/src/utils/mcpUi.test.ts`
- Modify: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: Write the failing cleanup/render tests**

```python
def test_delete_mcp_server_removes_api_monitor_child_tools(client, seeded_api_monitor_server, seeded_api_monitor_tools):
    response = client.delete("/api/v1/mcp/servers/mcp_api_monitor", headers=_auth_headers())
    assert response.status_code == 200
    assert seeded_api_monitor_tools_repo.remaining() == []
```

```ts
it('labels api monitor entries distinctly in My MCP', () => {
  const result = buildUnifiedUserMcpItems(
    [{ id: 'mcp_api_monitor', server_key: 'user:mcp_api_monitor', name: 'Example MCP', transport: 'api_monitor', source_type: 'api_monitor' }],
    [],
  )

  expect(result[0].kind).toBe('server')
  expect(result[0].server.transport).toBe('api_monitor')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_mcp_route.py -k delete_mcp_server_removes_api_monitor_child_tools -v`

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- mcpUi.test.ts`

Expected: FAIL because delete does not cascade and the UI has no API Monitor source treatment.

- [ ] **Step 3: Implement delete cleanup and UI hint**

```python
@router.delete("/mcp/servers/{server_id}", response_model=ApiResponse)
async def delete_mcp_server(...):
    repo = get_repository("user_mcp_servers")
    doc = await repo.find_one({"_id": server_id, "user_id": str(current_user.id)})
    if not doc:
        raise HTTPException(status_code=404, detail="MCP server not found")
    deleted = await repo.delete_one({"_id": server_id, "user_id": str(current_user.id)})
    if doc.get("source_type") == "api_monitor":
        await get_repository("api_monitor_mcp_tools").delete_many({"mcp_server_id": server_id, "user_id": str(current_user.id)})
    return ApiResponse(data={"id": server_id, "deleted": bool(deleted)})
```

```vue
<span v-if="item.server.transport === 'api_monitor'" class="badge-teal">
  {{ t('API Monitor MCP') }}
</span>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_mcp_route.py -k delete_mcp_server_removes_api_monitor_child_tools -v`

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- mcpUi.test.ts`

Expected: PASS with child tools removed on delete and My MCP entries labeled clearly.

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/route/mcp.py RpaClaw/backend/tests/test_mcp_route.py RpaClaw/frontend/src/pages/ToolsPage.vue RpaClaw/frontend/src/utils/mcpUi.test.ts
git commit -m "fix: align api monitor mcp deletion and rendering"
```

## Task 8: Full verification and doc touch-up

**Files:**
- Modify: `docs/superpowers/specs/2026-04-23-api-monitor-save-as-mcp-design.md`
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
- Test: `RpaClaw/backend/tests/test_mcp_route.py`
- Test: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts`
- Test: `RpaClaw/frontend/src/utils/mcpUi.test.ts`

- [ ] **Step 1: Run the focused backend test suite**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_mcp_route.py -v`

Expected: PASS for publish, overwrite, discovery, and delete-cascade cases.

- [ ] **Step 2: Run the focused frontend test suite**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm test -- ApiMonitorPage.test.ts mcpUi.test.ts`

Expected: PASS for save dialog, overwrite confirm, and My MCP rendering.

- [ ] **Step 3: Run one manual smoke path**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend
npm run dev
```

Then verify in the browser:

1. Open `/rpa/api-monitor`
2. Start a session and seed at least one tool
3. Confirm there is no single-tool save action
4. Click `Save as MCP Tool`
5. Save once with a fresh name
6. Save again with the same name and confirm overwrite
7. Open `/chat/tools` and confirm the MCP appears under `My MCP`
8. Open the tools dialog and confirm the saved API tools are listed

Expected: The saved MCP appears exactly once, and its tool list reflects the latest session result after overwrite.

- [ ] **Step 4: Update the design doc if implementation names drifted**

```md
- If the implementation settles on `transport="api_monitor"` plus `source_type="api_monitor"`, update the spec wording to match.
- If the route path or response shape changed during implementation, correct the spec so it documents the final contract.
```

- [ ] **Step 5: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add docs/superpowers/specs/2026-04-23-api-monitor-save-as-mcp-design.md
git add RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_mcp_route.py
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.test.ts RpaClaw/frontend/src/utils/mcpUi.test.ts
git commit -m "test: verify api monitor save as mcp flow"
```

## Self-Review

### Spec coverage

- Save button replacement: Task 5
- Remove single-tool save: Task 5
- One MCP containing many API tools: Tasks 1-3
- Duplicate-name confirm before overwrite: Tasks 2 and 5
- Replace tools on overwrite: Task 3
- Show in My MCP: Tasks 6 and 7
- `local` vs non-`local` repository abstraction: Tasks 1 and 6 via `get_repository(...)`

No uncovered spec section remains.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Each test step names a concrete command.
- Each code step includes concrete code blocks rather than vague descriptions.

### Type consistency

- Publish request fields use `mcp_name`, `description`, `confirm_overwrite` consistently.
- Persisted child tools use `mcp_server_id` consistently.
- Internal MCP identity stays `server_id`, while `session_id` remains only the temporary source of the publish action.

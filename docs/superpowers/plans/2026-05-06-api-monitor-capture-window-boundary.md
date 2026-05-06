# API Monitor 采集窗口边界实现计划

> **给 agentic worker 的要求：** 实现本计划时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项执行并用 checkbox（`- [ ]`）跟踪进度。

**目标：** 保证 API Monitor 只使用“分析或录制窗口内捕获到的 API”生成工具，同时保留窗口外 API 作为 token/auth 证据，不影响 token flow 检测和运行时注入。

**架构：** 将 API 调用按用途拆成两类：`captured_calls` 表示可用于生成工具的窗口内调用，`evidence_calls` 表示只能用于 token/auth 分析的上下文调用。录制和分析流程在正式窗口开始前 drain 到的调用只进入 `evidence_calls`，窗口内 drain 到的调用才进入 `_process_captured_calls_for_generation()`。

**技术栈：** FastAPI 后端、Python、Pydantic v2、pytest、现有 API Monitor manager/capture 测试体系。

---

## 文件结构

- 修改：`RpaClaw/backend/rpa/api_monitor/models.py`
  - 给 `ApiMonitorSession` 增加 evidence-only 调用列表。
- 修改：`RpaClaw/backend/rpa/api_monitor/manager.py`
  - 增加 evidence-only 存储 helper。
  - 修改录制开始、自由分析、定向分析的 pre-drain 行为。
  - 修改 token flow 读取来源，使其同时读取 evidence 和 generation calls。
- 修改：`RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`
  - 增加录制窗口边界测试。
- 修改：`RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`
  - 增加分析窗口边界测试和 token flow 兼容测试。

---

## 语义约束

1. `start_recording()` 之前已经捕获在 buffer 中的调用，不能创建 `ApiToolGenerationCandidate`。
2. 自动分析 probe 之前 drain 到的调用，不能创建 `ApiToolGenerationCandidate`。
3. 定向分析正式 step 之前 drain 到的调用，不能创建 `ApiToolGenerationCandidate`。
4. 录制 drain loop、录制停止 final drain、自由分析 probe、定向分析失败 step、定向分析完成 step 捕获到的调用，可以创建或更新候选工具。
5. 窗口外调用仍然必须参与 token flow 检测，不能因为禁止生成工具而丢失 CSRF/XSRF/auth 证据。
6. 修改完成后，`session.captured_calls` 只表示“可生成工具”的调用。
7. 新增 `session.evidence_calls` 表示“仅作为上下文证据”的调用。

---

### 任务 1：增加 Evidence-Only Session 存储

**文件：**
- 修改：`RpaClaw/backend/rpa/api_monitor/models.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **步骤 1：编写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py` 的 processing helper tests 附近添加：

```python
def test_session_separates_generation_calls_from_evidence_calls():
    from backend.rpa.api_monitor.models import ApiMonitorSession

    session = ApiMonitorSession(target_url="https://example.com")
    evidence_call = _call("csrf-call", method="GET", path="/api/csrf")
    generation_call = _call("orders-call", method="GET", path="/api/orders")

    session.evidence_calls.append(evidence_call)
    session.captured_calls.append(generation_call)

    assert session.evidence_calls == [evidence_call]
    assert session.captured_calls == [generation_call]
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::test_session_separates_generation_calls_from_evidence_calls -q
```

预期：失败，原因是 `evidence_calls` 尚未定义。

- [ ] **步骤 3：增加模型字段**

在 `RpaClaw/backend/rpa/api_monitor/models.py` 中更新 `ApiMonitorSession`：

```python
class ApiMonitorSession(BaseModel):
    id: str = Field(default_factory=_gen_id)
    target_url: str
    status: str = "idle"  # idle, analyzing, recording, stopped
    tool_definitions: List[ApiToolDefinition] = Field(default_factory=list)
    captured_calls: List[CapturedApiCall] = Field(default_factory=list)
    evidence_calls: List[CapturedApiCall] = Field(default_factory=list)
    token_flows: List[ApiTokenFlow] = Field(default_factory=list)
    generation_candidates: List[ApiToolGenerationCandidate] = Field(default_factory=list)
    directed_traces: List[DirectedAnalysisTrace] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

- [ ] **步骤 4：运行模型测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::test_session_separates_generation_calls_from_evidence_calls -q
```

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "fix: separate api monitor evidence calls"
```

---

### 任务 2：增加 Evidence-Only 处理 Helper

**文件：**
- 修改：`RpaClaw/backend/rpa/api_monitor/manager.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **步骤 1：编写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py` 的 `TestProcessCapturedCalls` 中添加：

```python
async def test_process_evidence_calls_does_not_create_generation_candidate(self):
    manager, session_id = _manager_with_session()
    call = _call("csrf-call", method="GET", path="/api/csrf")
    enqueued: list[tuple[str, str]] = []

    def fake_enqueue(session_id_arg: str, candidate_id: str, **kwargs):
        enqueued.append((session_id_arg, candidate_id))

    with patch.object(manager, "_enqueue_generation_candidate", side_effect=fake_enqueue):
        added = manager._store_evidence_calls(session_id, [call])

    session = manager.sessions[session_id]
    assert added == [call]
    assert session.evidence_calls == [call]
    assert session.captured_calls == []
    assert session.generation_candidates == []
    assert enqueued == []

async def test_store_evidence_calls_deduplicates_against_generation_and_evidence_calls(self):
    manager, session_id = _manager_with_session()
    evidence_call = _call("csrf-call", method="GET", path="/api/csrf")
    generated_call = _call("orders-call", method="GET", path="/api/orders")
    session = manager.sessions[session_id]
    session.evidence_calls.append(evidence_call)
    session.captured_calls.append(generated_call)

    added = manager._store_evidence_calls(session_id, [evidence_call, generated_call])

    assert added == []
    assert session.evidence_calls == [evidence_call]
    assert session.captured_calls == [generated_call]
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_process_evidence_calls_does_not_create_generation_candidate tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_store_evidence_calls_deduplicates_against_generation_and_evidence_calls -q
```

预期：失败，原因是 `_store_evidence_calls` 尚未定义。

- [ ] **步骤 3：实现 evidence helper**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 的 `_process_captured_calls_for_generation()` 附近添加：

```python
    def _store_evidence_calls(
        self,
        session_id: str,
        calls: list[CapturedApiCall],
    ) -> list[CapturedApiCall]:
        if not calls:
            return []
        session = self._require_session(session_id)
        existing_ids = {
            *(call.id for call in session.captured_calls),
            *(call.id for call in session.evidence_calls),
        }
        added: list[CapturedApiCall] = []
        for call in calls:
            if call.id in existing_ids:
                continue
            session.evidence_calls.append(call)
            existing_ids.add(call.id)
            added.append(call)
        return added

    def _token_flow_calls(self, session_id: str) -> list[CapturedApiCall]:
        session = self._require_session(session_id)
        by_id: dict[str, CapturedApiCall] = {}
        for call in [*session.evidence_calls, *session.captured_calls]:
            by_id.setdefault(call.id, call)
        return list(by_id.values())
```

- [ ] **步骤 4：运行 helper 测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_process_evidence_calls_does_not_create_generation_candidate tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_store_evidence_calls_deduplicates_against_generation_and_evidence_calls -q
```

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "fix: add api monitor evidence-only call storage"
```

---

### 任务 3：修复录制窗口边界

**文件：**
- 修改：`RpaClaw/backend/rpa/api_monitor/manager.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **步骤 1：编写失败测试**

在 `TestProcessCapturedCalls` 中添加：

```python
async def test_start_recording_stores_pre_calls_as_evidence_only(self):
    manager, session_id = _manager_with_session()
    pre_call = _call("csrf-call", method="GET", path="/api/csrf")
    session = manager.sessions[session_id]

    class _Capture:
        def __init__(self):
            self.calls = [pre_call]

        def drain_new_calls(self):
            calls = list(self.calls)
            self.calls = []
            return calls

    manager._captures[session_id] = _Capture()
    manager._enqueue_generation_candidate = lambda *args, **kwargs: None

    await manager.start_recording(session_id)
    await manager._stop_recording_drain_task(session_id)

    assert session.evidence_calls == [pre_call]
    assert session.captured_calls == []
    assert session.generation_candidates == []
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_start_recording_stores_pre_calls_as_evidence_only -q
```

预期：失败，因为 `start_recording()` 当前会把 pre-calls 送入工具生成链路。

- [ ] **步骤 3：修改 `start_recording()` 的 pre-drain 行为**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 中，把 `start_recording()` 内处理 `pre_calls` 的逻辑替换为：

```python
            pre_calls = capture.drain_new_calls()
            if pre_calls:
                added = self._store_evidence_calls(session_id, pre_calls)
                logger.info(
                    "[ApiMonitor] Stored %d pre-recording calls as evidence for session %s",
                    len(added),
                    session_id,
                )
```

不要对 pre-recording calls 调用 `_process_captured_calls_for_generation()`。

- [ ] **步骤 4：运行录制边界测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_start_recording_stores_pre_calls_as_evidence_only tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_recording_drain_loop_processes_calls_before_stop tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_recording_drain_stop_waits_for_in_flight_processing -q
```

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "fix: restrict recording generation to recording window"
```

---

### 任务 4：修复自由分析和定向分析的 Pre-Drain 边界

**文件：**
- 修改：`RpaClaw/backend/rpa/api_monitor/manager.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **步骤 1：编写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 中添加：

```python
def test_free_analysis_pre_probe_calls_are_evidence_only(monkeypatch):
    manager, session = _manager_with_session_for_analysis()
    historical = _captured_call(call_id="csrf-call", method="GET", path="/api/csrf")
    probed = _captured_call(call_id="orders-call", method="GET", path="/api/orders")
    manager._captures[session.id] = _FakeCapture(pre_calls=[historical], post_calls=[probed])

    async def fake_scan(_page):
        return [{"tag": "button", "text": "Search", "locator": "button"}]

    async def fake_classify(_session_id, elements, **_kwargs):
        return elements, []

    async def fake_probe(_page, _elem):
        return manager._captures[session.id].drain_new_calls()

    monkeypatch.setattr(manager, "_scan_interactive_elements", fake_scan)
    monkeypatch.setattr(manager, "_classify_elements", fake_classify)
    monkeypatch.setattr(manager, "_probe_element", fake_probe)
    monkeypatch.setattr(manager, "_enqueue_generation_candidate", lambda *args, **kwargs: None)

    events = asyncio.run(_collect_async(manager.analyze_page(session.id)))

    assert any(event["event"] == "analysis_complete" for event in events)
    assert session.evidence_calls == [historical]
    assert session.captured_calls == [probed]
    assert [candidate.source_call_ids for candidate in session.generation_candidates] == [[probed.id]]

def test_directed_analysis_pre_step_calls_are_evidence_only(monkeypatch):
    manager, session = _manager_with_session_for_analysis()
    historical = _captured_call(call_id="csrf-call", method="GET", path="/api/csrf")
    manager._captures[session.id] = _FakeCapture(pre_calls=[historical], post_calls=[])

    observations = [
        {"url": "https://example.com", "title": "Orders", "dom_digest": "before", "elements": []},
        {"url": "https://example.com", "title": "Orders", "dom_digest": "after", "elements": []},
    ]

    async def fake_observe(_page, _instruction):
        return observations.pop(0) if observations else observations[-1]

    async def fake_plan(*_args, **_kwargs):
        return {"action": "done", "reason": "done"}

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.plan_directed_action", fake_plan)
    monkeypatch.setattr(manager, "_enqueue_generation_candidate", lambda *args, **kwargs: None)

    events = asyncio.run(_collect_async(manager.analyze_directed_page(session.id, instruction="find orders")))

    assert any(event["event"] == "analysis_complete" for event in events)
    assert session.evidence_calls == [historical]
    assert session.captured_calls == []
    assert session.generation_candidates == []
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_analysis_modes.py::test_free_analysis_pre_probe_calls_are_evidence_only tests/test_api_monitor_analysis_modes.py::test_directed_analysis_pre_step_calls_are_evidence_only -q
```

预期：失败，因为 pre-drain calls 当前会进入 `_process_captured_calls_for_generation()`。

- [ ] **步骤 3：修改分析 pre-drain 逻辑**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 中，将 pre-drain 场景里的：

```python
await self._process_captured_calls_for_generation(session_id, pre_calls)
```

替换为：

```python
self._store_evidence_calls(session_id, pre_calls)
```

应用范围：

- `analyze_page()` 中 probe 前的 drain。
- `analyze_directed_page()` 初始 pre-drain。

不要修改 `probed_calls`、`failed_step_calls`、`step_calls`，这些属于窗口内调用，仍然必须调用 `_process_captured_calls_for_generation(..., model_config=model_config)`。

- [ ] **步骤 4：运行分析边界测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_analysis_modes.py::test_free_analysis_pre_probe_calls_are_evidence_only tests/test_api_monitor_analysis_modes.py::test_directed_analysis_pre_step_calls_are_evidence_only -q
```

预期：通过。

- [ ] **步骤 5：提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "fix: restrict analysis generation to analysis window"
```

---

### 任务 5：保证 Token Flow 使用 Evidence Calls

**文件：**
- 修改：`RpaClaw/backend/rpa/api_monitor/manager.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **步骤 1：编写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 中添加：

```python
def test_token_flow_detection_uses_evidence_calls_without_generating_tools(monkeypatch):
    manager, session = _manager_with_session_for_analysis()
    csrf_call = _captured_call(
        call_id="csrf-call",
        method="GET",
        path="/api/csrf",
        response_body='{"csrfToken":"abc123"}',
    )
    orders_call = _captured_call(
        call_id="orders-call",
        method="POST",
        path="/api/orders",
        request_headers={"x-csrf-token": "abc123"},
        request_body='{"name":"demo"}',
    )
    session.evidence_calls.append(csrf_call)
    session.captured_calls.append(orders_call)

    monkeypatch.setattr(manager, "_enqueue_generation_candidate", lambda *args, **kwargs: None)

    flows = manager.detect_token_flows(session.id)

    assert flows
    assert session.evidence_calls == [csrf_call]
    assert session.captured_calls == [orders_call]
    assert session.generation_candidates == []
```

如果当前 token flow 方法名称不是 `detect_token_flows`，使用 `manager.py` 中现有读取 `session.captured_calls` 的 token flow 方法；断言保持一致。

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_analysis_modes.py::test_token_flow_detection_uses_evidence_calls_without_generating_tools -q
```

预期：失败，因为 token flow 当前只读取 `session.captured_calls`。

- [ ] **步骤 3：修改 token flow 调用来源**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 中查找 token-flow 检测和 auth 推断相关方法里读取：

```python
session.captured_calls
```

的地方。仅在 token-flow/auth inference 场景，将来源替换为：

```python
self._token_flow_calls(session_id)
```

候选生成、候选 reconcile、工具生成仍然只能读取 `session.captured_calls`。

- [ ] **步骤 4：确认 reconcile 不读取 evidence calls**

确认 `reconcile_generation_candidates()` 保持如下语义：

```python
for call in session.captured_calls:
    candidate, created = self._upsert_generation_candidate(session_id, call)
```

不要改成 `_token_flow_calls(session_id)`。

- [ ] **步骤 5：运行 token flow 和候选测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_analysis_modes.py::test_token_flow_detection_uses_evidence_calls_without_generating_tools tests/test_api_monitor_realtime_generation.py::TestProcessCapturedCalls::test_process_evidence_calls_does_not_create_generation_candidate -q
```

预期：通过。

- [ ] **步骤 6：提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "fix: preserve token flow from evidence calls"
```

---

### 任务 6：完整回归验证

**文件：**
- 仅验证。

- [ ] **步骤 1：运行 API Monitor 后端测试**

运行：

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_realtime_generation.py tests/test_api_monitor_analysis_modes.py tests/test_api_monitor_capture.py -q
```

预期：全部通过。

- [ ] **步骤 2：运行前端 API Monitor 测试**

运行：

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
```

预期：全部通过。

- [ ] **步骤 3：构建前端**

运行：

```bash
cd RpaClaw/frontend
npm run build
```

预期：构建通过。现有重复 i18n key、CSS、Browserslist、chunk-size warning 可以保留，除非其他任务已经处理。

- [ ] **步骤 4：手动烟测**

执行下面的 API Monitor 验证：

1. 打开一个会在录制前触发 `/api/csrf` 或 `/api/login` 的页面。
2. 点击开始录制。
3. 在录制过程中触发 `/api/orders`。
4. 点击停止录制。
5. 确认 `/api/orders` 出现在已生成或生成中的候选工具里。
6. 确认录制前的 `/api/csrf` 不会作为独立工具生成。
7. 确认 `/api/orders` 生成的工具仍能使用检测到的 CSRF/token flow。

- [ ] **步骤 5：如有验证 fixture 改动则提交**

如果没有文件变化，不提交。如果测试过程中需要微调 fixture，则提交：

```bash
git add RpaClaw/backend/tests/test_api_monitor_realtime_generation.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "test: cover api monitor capture window boundary"
```

---

## 自检

- 需求覆盖：
  - “仅记录在分析或录制过程中的 API 作为生成工具的依赖”：任务 3、任务 4 覆盖。
  - “不影响 token flow 的过程”：任务 2、任务 5 覆盖。
- 占位符检查：
  - 没有 `TBD`、`TODO`、`implement later` 等空洞占位。
- 类型一致性：
  - `evidence_calls` 定义在 `ApiMonitorSession`。
  - `_store_evidence_calls()` 只保存 evidence，不触发候选工具生成。
  - `_token_flow_calls()` 只用于 token flow/auth inference。
  - `session.captured_calls` 保持“可生成工具调用”的含义。

# API Monitor 实时工具生成设计

日期：2026-04-30

## 1. 背景

API Monitor 现在在两个地方生成 MCP 工具：

- 手动录制：`stop_recording()` drain 新捕获的 API calls，然后调用 `_generate_tools_from_calls(...)`。
- 定向分析：`analyze_directed_page()` 在分析 loop 结束后，把本轮 `directed_calls` 一次性交给 `_generate_tools_from_calls(...)`。

这个流程可靠但滞后。更重要的是，工具生成时扫描的是结束时的 DOM，而不是 API 被触发时的页面上下文。对于搜索、筛选、分页、弹窗、步骤式表单等页面，结束时 DOM 可能已经不是触发 API 的 DOM，参数推断和工具描述会变弱。

本设计把 API 捕获和 LLM 工具生成拆成两条路径：

```text
捕获路径：实时、轻量、不可丢。
生成路径：异步、限流、幂等、可重试。
```

## 2. 目标

- 录制和分析过程中，只要捕获到 API，就立即在前端出现候选占位项。
- 后台异步生成 MCP tool 草稿，生成完成后实时更新工具列表。
- 同一 API endpoint 的重复调用只追加样本，不重复创建 LLM 任务。
- 后台 worker 崩溃、LLM 失败或 429 时，不丢失任何已捕获 API。
- 是否采用工具继续复用现有 confidence/score/selected 机制。
- 发布 MCP 时仍只发布 `selected == true` 的已生成工具。
- 捕获时保存轻量 DOM 上下文，减少结束时 DOM 漂移对参数推断的影响。

## 3. 非目标

- 不把 API Monitor 改成普通 RPA trace 编译链路。
- 不新增一套“是否采用”判断系统。
- 不在网络捕获阶段调用 LLM。
- 不因为工具生成失败阻塞录制、定向分析或手动浏览。
- 不引入长期站点经验库、selector 经验库或规则驱动 Agent。
- 第一版不要求跨进程持久队列；session 内存态配合 captured calls 重放即可。

## 4. 推荐架构

采用“实时捕获 + 幂等后台生成队列”。

```text
NetworkCaptureEngine 捕获 API response
        ->
append session.captured_calls
        ->
按 dedup key upsert generation candidate
        ->
emit api_candidate_created / api_candidate_updated
        ->
后台 worker 限流消费 candidate
        ->
用捕获时 DOM context + samples 调 LLM 生成 YAML
        ->
复用现有 confidence 系统设置 score/confidence/selected
        ->
写入或更新 session.tool_definitions
        ->
emit api_tool_generated
```

关键边界：

- `CapturedApiCall` 是事实源。
- `ApiToolGenerationCandidate` 是实时占位和生成状态。
- `ApiToolDefinition` 是生成后的可编辑 MCP tool 草稿。
- LLM 生成结果是可重建缓存，不是事实源。

## 5. 数据模型

### 5.1 Generation candidate

在 `backend/rpa/api_monitor/models.py` 中新增模型：

```python
GenerationStatus = Literal[
    "pending",
    "running",
    "generated",
    "failed",
    "rate_limited",
    "stale",
]


class ApiToolGenerationCandidate(BaseModel):
    id: str = Field(default_factory=_gen_id)
    session_id: str
    dedup_key: str
    method: str
    url_pattern: str

    source_call_ids: List[str] = Field(default_factory=list)
    sample_call_ids: List[str] = Field(default_factory=list)

    status: GenerationStatus = "pending"
    tool_id: Optional[str] = None
    error: str = ""
    retry_after: Optional[datetime] = None
    attempts: int = 0

    capture_dom_context: Dict = Field(default_factory=dict)
    capture_page_url: str = ""
    capture_title: str = ""
    capture_dom_digest: str = ""

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

`ApiMonitorSession` 增加：

```python
generation_candidates: List[ApiToolGenerationCandidate] = Field(default_factory=list)
```

### 5.2 Tool definition

`ApiToolDefinition` 保留现有字段：

- `confidence`
- `score`
- `selected`
- `confidence_reasons`
- `source_evidence`

可选增加：

```python
generation_candidate_id: Optional[str] = None
```

这个字段只用于把已生成 tool 反查到占位项，不参与发布判断。

### 5.3 DOM context 策略

实时生成不再只依赖 `_generate_tools_from_calls(...)` 结束时扫描 DOM。

第一版策略：

- 新 dedup group 第一次出现时，扫描轻量 DOM context 并保存到 candidate。
- 后续同 group 新样本只追加 `source_call_ids` 和 `sample_call_ids`。
- 生成时优先使用 candidate 保存的 `capture_dom_context`。
- 如果没有捕获时 DOM context，再 fallback 到当前页面 DOM 扫描。

保留第一次触发时 DOM，是因为它最接近“这个 API 为什么出现”的页面状态。后续可以扩展 `dom_digest_variants`，但第一版不需要。

## 6. 去重与幂等

候选层使用现有 API 分组思想，dedup key 应与工具去重尽量一致：

```text
method + parameterized path
```

重复请求处理规则：

- 如果 dedup key 不存在：创建 candidate，状态 `pending`，入队。
- 如果 candidate 是 `pending`：追加样本，不重复入队。
- 如果 candidate 是 `running`：追加样本；worker 完成后发现样本变化，标记 `stale` 并重新入队。
- 如果 candidate 是 `generated`：追加样本后标记 `stale`，低优先级重生成。
- 如果 candidate 是 `failed` 或 `rate_limited`：追加样本，等待用户重试或 retry_after。

每个 session 同一 dedup key 同时最多一个 running job。

## 7. 后台 worker

### 7.1 并发限制

推荐第一版限制：

- 全局 LLM 生成并发：1-2。
- 单 session 生成并发：1。
- 同 dedup key 并发：1。

这能显著降低 429，同时避免同一页面大量 API 同时生成导致状态抖动。

### 7.2 Worker 输入

worker 根据 candidate 读取：

- candidate 的 sample call IDs。
- 对应 `CapturedApiCall` samples。
- candidate 保存的 DOM context。
- session target URL 或 capture page URL。

然后调用现有 `generate_tool_definition(...)`。

### 7.3 成功路径

生成 YAML 后：

1. 解析 name 和 description。
2. 创建或更新 `ApiToolDefinition`。
3. 调用现有 `_apply_confidence_to_tool(tool, samples)`。
4. 调用现有 `_dedup_session_tools(...)`。
5. 设置 candidate `status = "generated"`，`tool_id = tool.id`。
6. emit `api_tool_generated`。

是否默认采用由现有 confidence 系统决定：

```text
high   -> selected = true
medium -> selected = false
low    -> selected = false
```

当前代码阈值以实现为准：`score >= 80` 映射 high。历史文档中出现过 `score >= 75`，本设计不调整阈值。

## 8. 崩溃恢复

捕获路径必须先写入 `session.captured_calls`，再处理候选和后台队列。因此 worker 崩溃只会影响生成延迟，不会丢 API。

新增内部方法：

```python
reconcile_generation_candidates(session_id: str) -> None
```

职责：

1. 从 `session.captured_calls` 按 dedup key 重建缺失 candidate。
2. 修复 candidate 中缺失的 source call IDs。
3. 对 `pending / failed / rate_limited / stale` 且到达重试时间的 candidate 重新入队。
4. 不覆盖用户已经编辑过的 tool YAML。

调用时机：

- `stop_recording()` drain 剩余 calls 后。
- `analyze_page()` 和 `analyze_directed_page()` 结束前。
- `GET /generation-candidates` 时可轻量 reconcile。
- 手动 retry 前。

## 9. 429 与错误恢复

### 9.1 Rate limit

worker 捕获 provider 429 或明确 rate limit 错误时：

```text
candidate.status = "rate_limited"
candidate.attempts += 1
candidate.retry_after = now + exponential_backoff + jitter
candidate.error = short error message
```

然后 emit `api_candidate_rate_limited`。

到达 `retry_after` 后，candidate 可以自动重新入队。达到最大重试次数后转为 `failed`，但 captured calls 仍保留。

### 9.2 普通失败

LLM 调用失败、YAML 解析失败或工具生成异常：

```text
candidate.status = "failed"
candidate.error = short error message
candidate.attempts += 1
```

前端显示“生成失败，可重试”。重试仍基于同一 candidate 的 source calls 和 DOM context。

## 10. 手动录制流程

`start_recording()`：

- 保持现有 pre-call drain 行为。
- 清理上一轮 recording cache。
- 不清空 session 历史 captured calls。

录制中：

- 新 API response 捕获后，立即进入 candidate upsert 和后台队列。
- 前端通过事件看到占位项。

`stop_recording()`：

- drain 剩余 calls。
- append captured calls。
- reconcile candidates。
- enqueue 未完成 candidates。
- 返回当前已生成 tools 和仍在生成的 candidates。

保守兼容：

- 如果后台队列不可用，可以 fallback 到现有 `_generate_tools_from_calls(...)`。

## 11. 自动与定向分析流程

`analyze_page()`：

- 每次 probe 后 drain 到的新 calls 立即进入 candidate upsert。
- 不等所有 probe 完成再统一生成。
- 结束时 reconcile 并返回当前状态。

`analyze_directed_page()`：

- 每个 directed step 后 drain 到的新 calls 立即进入 candidate upsert。
- `DirectedAnalysisTrace.captured_call_ids` 保持不变，继续记录事实证据。
- completion check 仍看 captured API summary，不依赖 tool 是否已经生成完成。
- 结束时不阻塞等待全部 LLM 生成完成。

## 12. SSE 与 API

### 12.1 SSE 事件

新增事件：

```text
api_candidate_created
api_candidate_updated
api_candidate_rate_limited
api_tool_generated
api_tool_generation_failed
```

事件数据示例：

```json
{
  "candidate_id": "cand_1",
  "dedup_key": "GET /api/orders/{id}",
  "method": "GET",
  "url_pattern": "/api/orders/{id}",
  "status": "running",
  "source_call_count": 3,
  "tool_id": null,
  "error": "",
  "retry_after": null
}
```

### 12.2 REST 接口

保留现有接口：

- `GET /api/v1/api-monitor/session/{session_id}/tools`
- `PATCH /api/v1/api-monitor/session/{session_id}/tools/{tool_id}/selection`
- `POST /api/v1/api-monitor/session/{session_id}/publish-mcp`

新增：

```text
GET  /api/v1/api-monitor/session/{session_id}/generation-candidates
POST /api/v1/api-monitor/session/{session_id}/generation-candidates/{candidate_id}/retry
```

发布 MCP 不读取 candidate，只读取 generated tool definitions 中 `selected == true` 的工具。

## 13. 前端体验

API Monitor 工具列表展示两类项：

1. 生成中候选占位项。
2. 已生成 tool 卡片。

候选占位项显示：

- method
- url pattern
- status：生成中、限流重试中、生成失败、已生成
- source call count
- retry_after 或 error

生成成功后，占位项替换为现有 tool 卡片，继续展示：

- score
- confidence
- confidence_reasons
- selected 开关
- 编辑
- 删除

采用逻辑不变。用户通过现有 selected 开关决定是否发布。

## 14. 发布语义

发布 API Monitor MCP 时：

- 只发布 `session.tool_definitions` 中 `selected == true` 的工具。
- `pending / running / failed / rate_limited` candidates 不会进入 MCP。
- 用户可以等待生成完成、重试失败项、编辑 tool，之后再发布。

这保持现有“候选可见，采用后发布”的产品边界。

## 15. 测试计划

后端测试：

- 同一 dedup key 多次捕获只创建一个 candidate。
- candidate running 时追加样本不会重复开 LLM job。
- 生成成功后调用现有 confidence，`selected` 与当前评分规则一致。
- 429 会标记 `rate_limited` 并设置 `retry_after`。
- 普通生成失败会标记 `failed`，captured calls 保留。
- reconcile 能从 `captured_calls` 重建缺失 candidate。
- stop recording 会 drain 剩余 calls、reconcile、enqueue 未完成 candidate。
- directed analysis 每步捕获的 calls 仍写入 trace 的 `captured_call_ids`。
- publish MCP 只发布 `selected == true` 的 generated tools。

前端测试：

- SSE candidate 事件会创建占位项。
- `api_tool_generated` 后占位项变成 tool 卡片。
- `rate_limited` 显示下次重试时间。
- `failed` 显示错误和重试按钮。
- selected 开关沿用现有接口。
- 未生成或未采用 candidate 不进入发布请求结果。

## 16. 风险与约束

- 后台生成可能仍然慢，所以 UI 必须清楚区分“已捕获”和“已生成”。
- 内存态队列在进程重启后会丢失，但 `captured_calls` 可用于 session 内恢复；如果未来 session 要持久化，再升级为持久队列。
- 同一 endpoint 在不同 DOM 状态下可能有不同语义。第一版按 dedup key 合并，后续可用 body shape 或 query shape 细分。
- 不能让 429 自动重试无限循环。必须有 attempts 上限和用户可见状态。

## 17. 实施顺序建议

1. 增加 candidate 模型和 manager 内部 upsert/reconcile 方法。
2. 增加受限后台 worker 和 429/失败状态。
3. 将录制和分析中的 drain calls 接入 candidate upsert。
4. 增加 REST/SSE candidate 事件。
5. 前端增加占位项展示和 retry。
6. 补后端与前端测试。


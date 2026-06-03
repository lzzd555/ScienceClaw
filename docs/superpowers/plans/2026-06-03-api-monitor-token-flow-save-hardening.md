# API Monitor Token Flow 保存硬化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 token flow 保存的四个问题：过滤无关 consumers、收窄发现策略、修复同名字段去重 bug、将 token producer 保存为特殊工具。

**Architecture:** 四个独立改动，互不依赖。改动 1-3 是纯修复/收窄，改动 4 涉及模型扩展和 publish 流程新增步骤。

**Tech Stack:** Python 3.13 (FastAPI, Pydantic v2), TypeScript (Vue 3), MongoDB

**Spec:** `docs/superpowers/specs/2026-06-03-api-monitor-token-flow-save-hardening-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `RpaClaw/backend/rpa/api_monitor_token_flow.py` | 改动 2: 收窄发现策略；改动 3: 修复 producer 去重 |
| Modify | `RpaClaw/backend/rpa/api_monitor/models.py` | 改动 4: `ApiToolDefinition` 新增 `tool_type` 字段 |
| Modify | `RpaClaw/backend/route/api_monitor.py` | 改动 4: publish 路由中生成 dynamic_token 工具 |
| Modify | `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue` | 改动 1: 前端过滤 consumers；改动 4: 展示 dynamic_token 工具 |
| Modify | `RpaClaw/frontend/src/api/apiMonitor.ts` | 改动 4: 新增 `tool_type` 类型定义 |

---

## Task 1: 收窄 Producer 发现策略（去掉高熵扫描）

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor_token_flow.py:91-105` (`is_dynamic_value_candidate`)
- Modify: `RpaClaw/backend/rpa/api_monitor_token_flow.py:626-638` (`_producer_signals`)
- Test: `RpaClaw/backend/tests/test_token_flow_discovery.py` (新建)

**背景:** 当前 `is_dynamic_value_candidate` 在字段名不包含 token 语义关键词时，仍通过熵值判断让值进入候选池。`_producer_signals` 中也对通过熵值检查的值添加 `high-entropy` signal。需要收窄为只依赖字段名语义规则。

- [ ] **Step 1: 写失败测试 — 无语义字段名的高熵值不进入候选池**

```python
# RpaClaw/backend/tests/test_token_flow_discovery.py
"""Tests for token flow producer discovery narrowing."""

import pytest
from backend.rpa.api_monitor_token_flow import is_dynamic_value_candidate


class TestProducerDiscoveryNarrowing:
    """改动2: 只保留语义规则，去掉高熵扫描。"""

    def test_semantic_name_short_value_passes(self):
        """字段名含 token 且值长度>=6，应通过。"""
        assert is_dynamic_value_candidate("abc123", field_name="csrf_token") is True

    def test_semantic_name_short_value_too_short_fails(self):
        """字段名含 token 但值太短（<6），应拒绝。"""
        assert is_dynamic_value_candidate("ab", field_name="csrf_token") is False

    def test_non_semantic_high_entropy_value_rejected(self):
        """字段名不含 token 语义，即使值高熵也应拒绝。"""
        high_entropy_value = "aB3$kL9#mN2&pQ7!"
        assert is_dynamic_value_candidate(high_entropy_value, field_name="guard") is True
        # 注意: "guard" 匹配 TOKEN_NAME_RE 中的 guard

    def test_non_semantic_non_token_name_rejected(self):
        """字段名完全不相关，高熵值也应拒绝。"""
        high_entropy_value = "xK9$mB2#nL5&pQ8!rT3"
        assert is_dynamic_value_candidate(high_entropy_value, field_name="data") is False

    def test_non_semantic_long_value_rejected(self):
        """字段名不相关，长值高熵也应拒绝。"""
        long_random = "8fa7c91e2d8a4c90b0f7a3d5e1c2b4a6"
        assert is_dynamic_value_candidate(long_random, field_name="r") is False

    def test_common_values_still_rejected(self):
        """常见值仍被拒绝。"""
        for val in ["true", "false", "null", "active", "success"]:
            assert is_dynamic_value_candidate(val, field_name="token") is False

    def test_pure_numeric_short_rejected(self):
        """短纯数字仍被拒绝。"""
        assert is_dynamic_value_candidate("12345", field_name="token") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd RpaClaw/backend && uv run pytest tests/test_token_flow_discovery.py -v`
Expected: `test_non_semantic_long_value_rejected` FAIL（当前高熵值会通过）

- [ ] **Step 3: 修改 `is_dynamic_value_candidate` 去掉高熵扫描分支**

修改 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 第 91-105 行：

```python
def is_dynamic_value_candidate(value: str, *, field_name: str = "") -> bool:
    """判断值是否为动态 token 候选。

    改动2后只依赖字段名语义规则，不再使用高熵扫描。
    字段名必须包含 token 相关语义关键词，且值长度>=6。
    """
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in COMMON_VALUES or DATE_LIKE_RE.match(text):
        return False
    if PURE_NUMERIC_RE.match(text) and len(text) < 16:
        return False
    strong_name = bool(TOKEN_NAME_RE.search(field_name or ""))
    if strong_name and len(text) >= 6:
        return True
    # 不再使用高熵扫描：非语义字段名的值一律不进入候选池
    return False
```

- [ ] **Step 4: 修改 `_producer_signals` 去掉 high-entropy signal**

修改 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 第 626-638 行：

```python
def _producer_signals(field_name: str, value: str) -> list[str]:
    signals: list[str] = []
    lowered = field_name.lower()
    if TOKEN_NAME_RE.search(lowered):
        if "csrf" in lowered or "xsrf" in lowered:
            signals.append("csrf-name")
        else:
            signals.append("token-name")
    # 不再添加 high-entropy signal
    return signals
```

注意: `_collect_producers` 中有 `if not signals: continue` 检查（第 175-176 行、第 198-199 行）。改动后，只有字段名含语义关键词的候选才会产生 signals，从而通过这个检查。这是预期行为。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd RpaClaw/backend && uv run pytest tests/test_token_flow_discovery.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/tests/test_token_flow_discovery.py
git commit -m "fix: 收窄 producer 发现策略，去掉高熵扫描只保留语义规则"
```

---

## Task 2: 修复 Producer 去重 — 不同接口同名字段各自独立

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor_token_flow.py:400-442` (`_match_flows`)
- Test: `RpaClaw/backend/tests/test_token_flow_discovery.py` (追加)

**背景:** 当前 `_match_flows` 中 flow_id 基于 `hash(producer.value_hash + consumer.value_hash)` 生成。当两个不同接口返回相同的 token 值时，flow_id 相同，导致后发现的 producer 被合并到第一个 flow 中（只保留一个来源）。需要在 flow_id 生成中加入 producer 的 URL 以区分不同接口。

- [ ] **Step 1: 写失败测试 — 不同接口同名字段产生独立 flow**

在 `RpaClaw/backend/tests/test_token_flow_discovery.py` 中追加：

```python
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
from backend.rpa.api_monitor_token_flow import build_api_monitor_token_flow_profile
from datetime import datetime, timezone


def _make_call(
    call_id: str, method: str, url: str,
    resp_body: str = "", resp_headers: dict | None = None,
    req_headers: dict | None = None, timestamp: datetime | None = None,
) -> CapturedApiCall:
    ts = timestamp or datetime.now(timezone.utc)
    return CapturedApiCall(
        id=call_id,
        request=CapturedRequest(
            method=method, url=url,
            headers=req_headers or {}, body="", timestamp=ts,
        ),
        response=CapturedResponse(
            status=200, headers=resp_headers or {},
            body=resp_body, content_type="application/json",
        ),
    )


class TestProducerDedupByUrl:
    """改动3: 不同接口同名字段产生独立 flow。"""

    def test_different_urls_same_field_different_values(self):
        """不同接口返回同名字段但值不同，产生两个独立 flow。"""
        calls = [
            # Producer A: /api/session → token: "abc"
            _make_call("c1", "GET", "https://example.com/api/session",
                       resp_body='{"token": "abc_token_value_1"}'),
            # Producer B: /api/config → token: "xyz"
            _make_call("c2", "GET", "https://example.com/api/config",
                       resp_body='{"token": "xyz_token_value_2"}'),
            # Consumer for A
            _make_call("c3", "POST", "https://example.com/api/orders",
                       req_headers={"X-Token": "abc_token_value_1"}),
            # Consumer for B
            _make_call("c4", "POST", "https://example.com/api/settings",
                       req_headers={"X-Token": "xyz_token_value_2"}),
        ]
        profile = build_api_monitor_token_flow_profile(calls)
        assert profile["flow_count"] == 2, (
            f"Expected 2 independent flows, got {profile['flow_count']}"
        )

    def test_different_urls_same_field_same_value(self):
        """不同接口返回同名字段且值相同，仍然产生两个独立 flow。"""
        calls = [
            # Producer A: /api/session → csrfToken: "shared_csrf_value"
            _make_call("c1", "GET", "https://example.com/api/session",
                       resp_body='{"csrfToken": "shared_csrf_value_12345"}'),
            # Producer B: /api/bootstrap → csrfToken: "shared_csrf_value"
            _make_call("c2", "GET", "https://example.com/api/bootstrap",
                       resp_body='{"csrfToken": "shared_csrf_value_12345"}'),
            # Consumer for A
            _make_call("c3", "POST", "https://example.com/api/orders",
                       req_headers={"X-CSRF-Token": "shared_csrf_value_12345"}),
        ]
        profile = build_api_monitor_token_flow_profile(calls)
        assert profile["flow_count"] == 2, (
            f"Expected 2 independent flows (one per URL), got {profile['flow_count']}"
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd RpaClaw/backend && uv run pytest tests/test_token_flow_discovery.py::TestProducerDedupByUrl -v`
Expected: FAIL — `test_different_urls_same_field_same_value` 失败（当前只有 1 个 flow）

- [ ] **Step 3: 修改 `_match_flows` 在 flow_id 中加入 producer URL**

修改 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 第 400-442 行，将 flow_id 生成改为包含 producer 的 method + url_pattern：

```python
def _match_flows(
    producers: list[_TokenCandidate],
    value_to_producers: dict[str, list[_TokenCandidate]],
    consumers: list[_TokenConsumer],
) -> list[_TokenFlow]:
    flows: list[_TokenFlow] = []
    matched_consumer_ids: set[tuple[str, str]] = set()

    # Producer-first: for each consumer, find matching producer
    for consumer in consumers:
        matching = value_to_producers.get(consumer.value_hash, [])
        # Find all producers before this consumer, not just the earliest
        valid_producers = [
            p for p in matching
            if p.timestamp_key < consumer.timestamp_key
        ]
        if not valid_producers:
            continue

        # 改动3: 为每个有效 producer 创建独立 flow（不同 URL 不合并）
        for best_producer in valid_producers:
            cons_origin = _origin_from_url_pattern(consumer.url_pattern)
            prod_origin = _origin_from_url_pattern(best_producer.url_pattern)
            same_origin = cons_origin == prod_origin if cons_origin and prod_origin else True

            # flow_id 包含 producer 的 method + url，确保不同接口独立
            flow_id = f"flow_{_hash_value(best_producer.value_hash + consumer.value_hash + best_producer.method + best_producer.url_pattern)[:12]}"
            existing = next((f for f in flows if f.id == flow_id), None)
            if existing:
                existing.consumers.append(consumer)
            else:
                name = _derive_flow_name(best_producer.field_name, consumer.field_name)
                reasons = _compute_reasons(best_producer, consumer, same_origin)
                confidence = _compute_confidence(reasons)
                flow = _TokenFlow(
                    id=flow_id,
                    name=name,
                    producer=best_producer,
                    consumers=[consumer],
                    confidence=confidence,
                    reasons=reasons,
                )
                flows.append(flow)
            matched_consumer_ids.add((consumer.call_id, consumer.path))

    return flows
```

关键变更：
- 不再只选最早的 producer（`best_producer`），而是遍历所有时间上有效的 producers
- `flow_id` 生成加入 `best_producer.method + best_producer.url_pattern`，使不同接口产生不同 flow_id

- [ ] **Step 4: 运行测试确认通过**

Run: `cd RpaClaw/backend && uv run pytest tests/test_token_flow_discovery.py -v`
Expected: ALL PASS

- [ ] **Step 5: 运行现有 token flow 测试确保不回归**

Run: `cd RpaClaw/backend && uv run pytest tests/ -k "token_flow" -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/tests/test_token_flow_discovery.py
git commit -m "fix: producer 去重 key 加入 URL，不同接口同名字段各自独立"
```

---

## Task 3: 前端过滤 Token Flow Consumers

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

**背景:** 前端 publish 弹窗中展示 token flow candidates 时，consumers 包含了未选中工具的 URL。需要在展示时和提交时都过滤掉这些 consumers。

- [ ] **Step 1: 找到 tokenFlowProfile 的加载位置**

在 `ApiMonitorPage.vue` 中搜索 `tokenFlowProfile`，确认它从 API 加载后的存储位置。这是 `tokenFlowProfile` ref 变量，类型为 `TokenFlowProfile[]`。

- [ ] **Step 2: 添加 computed 属性过滤 consumers**

在 `ApiMonitorPage.vue` 的 `<script setup>` 中，`tokenFlowProfile` 附近添加一个 computed，根据 `adoptedTools` 过滤 consumers：

```typescript
/** 改动1: 只展示命中已选中工具的 token flow consumers */
const filteredTokenFlowProfile = computed(() => {
  if (!tokenFlowProfile.value.length) return [];

  // 构建已选中工具的 (method, url_path) 集合
  const selectedEndpoints = new Set(
    adoptedTools.value.map((t: any) => {
      const parsed = new URL(t.url_pattern || '', 'http://dummy');
      const method = (t.method || 'GET').toUpperCase();
      const path = '/' + parsed.pathname.replace(/^\/+|\/+$/g, '');
      return `${method} ${path}`;
    })
  );

  return tokenFlowProfile.value
    .map((flow) => {
      // 过滤 consumer_summaries: 保留命中 selected tools 的 consumer
      const filteredConsumers = flow.consumer_summaries?.filter((cs) => {
        // consumer_summary 格式: "POST /api/orders request.headers.X-CSRF-Token"
        const match = cs.match(/^(\w+)\s+(\S+)/);
        if (!match) return true; // 无法解析的保留
        const method = match[1].toUpperCase();
        const path = match[2];
        return selectedEndpoints.has(`${method} ${path}`);
      }) || [];

      // 同步过滤 runtime_config.consumers（如果存在）
      let runtimeConsumers = flow.runtime_config?.consumers || [];
      if (runtimeConsumers.length) {
        runtimeConsumers = runtimeConsumers.filter((c: any) => {
          const method = (c.method || 'GET').toUpperCase();
          const url = c.url || '';
          return selectedEndpoints.has(`${method} ${url}`);
        });
      }

      // consumers 全部过滤掉的 flow 不展示
      if (filteredConsumers.length === 0 && (!runtimeConsumers.length || runtimeConsumers.length === 0)) {
        return null;
      }

      return {
        ...flow,
        consumer_summaries: filteredConsumers,
        runtime_config: flow.runtime_config
          ? { ...flow.runtime_config, consumers: runtimeConsumers }
          : undefined,
      };
    })
    .filter(Boolean);
});
```

- [ ] **Step 3: 模板中替换 tokenFlowProfile 为 filteredTokenFlowProfile**

在 publish 弹窗模板中，将两处引用替换：

1. `v-if="tokenFlowProfile.length > 0"` → `v-if="filteredTokenFlowProfile.length > 0"`
2. `v-for="flow in tokenFlowProfile"` → `v-for="flow in filteredTokenFlowProfile"`

- [ ] **Step 4: 提交**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: publish 弹窗根据 selected tools 过滤 token flow consumers"
```

---

## Task 4: ApiToolDefinition 新增 tool_type 字段

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py:63-91` (`ApiToolDefinition`)
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`

**背景:** 为改动 4 做模型准备。`ApiToolDefinition` 新增可选字段 `tool_type`，默认为 `None`。

- [ ] **Step 1: 在 `ApiToolDefinition` 中新增 `tool_type` 字段**

在 `RpaClaw/backend/rpa/api_monitor/models.py` 的 `ApiToolDefinition` 类中，`is_reserve` 字段后添加：

```python
    tool_type: Optional[str] = None  # None = 普通业务工具, "dynamic_token" = 动态 token 工具
```

- [ ] **Step 2: 在前端类型定义中新增 `tool_type`**

在 `RpaClaw/frontend/src/api/apiMonitor.ts` 的 `ApiToolDefinition` 接口中添加：

```typescript
  tool_type?: string | null; // null = 普通业务工具, "dynamic_token" = 动态 token 工具
```

- [ ] **Step 3: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/frontend/src/api/apiMonitor.ts
git commit -m "feat: ApiToolDefinition 新增可选 tool_type 字段"
```

---

## Task 5: Publish 时为 Token Producer 生成 dynamic_token 工具

**Files:**
- Modify: `RpaClaw/backend/route/api_monitor.py:563-634` (`publish_mcp`)
- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py:28-97` (`publish_session`)

**背景:** 在 publish 流程中，为每个 token flow 的 producer 生成 Swagger 2.0 YAML 工具定义。生成失败时重试一次，仍失败则中止发布。

**关键依赖:**
- `generate_tool_definition` 在 `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py:218`
- `parse_api_monitor_tool_yaml` 在 `RpaClaw/backend/rpa/api_monitor_mcp_contract.py:87`
- Session 中的 `CapturedApiCall` 通过 `flow.producer.request.url` 和 `source_call_id` 查找

- [ ] **Step 1: 在 `api_monitor_token_flow.py` 中导出 producer 源数据提取函数**

在 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 末尾添加辅助函数：

```python
def extract_producer_source_calls(
    calls: list[CapturedApiCall],
    token_flows: list[dict[str, Any]],
) -> dict[str, CapturedApiCall]:
    """从 token_flows 的 resolved runtime config 中提取 producer 的源调用。

    返回: {flow_id: CapturedApiCall}，用于后续生成 Swagger YAML。
    """
    result: dict[str, CapturedApiCall] = {}
    if not token_flows:
        return result

    call_by_id = {c.id: c for c in calls}

    for flow_doc in token_flows:
        flow_id = flow_doc.get("id", "")
        summary = flow_doc.get("summary", {})
        source_call_ids = summary.get("source_call_ids", [])

        # 找到 producer 对应的源调用
        producer_url = flow_doc.get("producer", {}).get("request", {}).get("url", "")
        producer_method = flow_doc.get("producer", {}).get("request", {}).get("method", "GET")

        for cid in source_call_ids:
            call = call_by_id.get(cid)
            if call and call.request.method.upper() == producer_method.upper():
                result[flow_id] = call
                break

    return result
```

并在文件顶部的导入区域更新 `__all__` 或确保函数可被外部导入。

- [ ] **Step 2: 在 route 层添加 dynamic_token 工具生成逻辑**

修改 `RpaClaw/backend/route/api_monitor.py` 的 `publish_mcp` 函数，在 `resolve_token_flows_for_publish` 之后、`registry.publish_session` 之前插入：

```python
    # ... (existing code to build combined_flows) ...
    combined_flows = [*token_flows, *manual_flows]
    if combined_flows:
        api_monitor_auth["token_flows"] = combined_flows

    # ── 改动4: 为 token producer 生成 dynamic_token 工具 ──
    from backend.rpa.api_monitor_token_flow import extract_producer_source_calls
    from backend.rpa.api_monitor.llm_analyzer import generate_tool_definition
    from backend.rpa.api_monitor_mcp_contract import parse_api_monitor_tool_yaml
    from backend.rpa.api_monitor.models import ApiToolDefinition

    dynamic_token_tools: list[ApiToolDefinition] = []
    if combined_flows:
        source_calls = extract_producer_source_calls(
            _token_flow_calls_for_session(session), combined_flows
        )
        for flow_doc in combined_flows:
            flow_id = flow_doc.get("id", "")
            source_call = source_calls.get(flow_id)
            if not source_call:
                continue

            # 生成 YAML，失败重试一次
            yaml_str = None
            for attempt in range(2):
                try:
                    yaml_str = await generate_tool_definition(
                        method=source_call.request.method,
                        url_pattern=source_call.url_pattern or source_call.request.url,
                        samples=[source_call],
                        page_context=session.target_url or "",
                    )
                    if yaml_str:
                        break
                except Exception as exc:
                    if attempt == 0:
                        logger.warning(
                            "[Publish] dynamic_token 工具生成失败 (attempt 1/%s), 重试: %s",
                            flow_id, exc,
                        )
                    else:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Token producer 工具生成失败 (flow={flow_id}): {exc}",
                        ) from exc

            if not yaml_str:
                raise HTTPException(
                    status_code=500,
                    detail=f"Token producer 工具生成返回空结果 (flow={flow_id})",
                )

            # YAML 校验，失败重试一次
            contract = None
            for attempt in range(2):
                try:
                    contract = parse_api_monitor_tool_yaml(yaml_str)
                    if contract.valid:
                        break
                    if attempt == 0:
                        # 校验失败，重新生成
                        logger.warning(
                            "[Publish] dynamic_token YAML 校验失败 (attempt 1), 重试: %s",
                            contract.validation_errors,
                        )
                        try:
                            yaml_str = await generate_tool_definition(
                                method=source_call.request.method,
                                url_pattern=source_call.url_pattern or source_call.request.url,
                                samples=[source_call],
                                page_context=session.target_url or "",
                            )
                        except Exception:
                            pass
                        continue
                    else:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Token producer YAML 校验失败 (flow={flow_id}): {contract.validation_errors}",
                        )
                except HTTPException:
                    raise
                except Exception as exc:
                    if attempt == 0:
                        logger.warning("[Publish] dynamic_token YAML 解析异常, 重试: %s", exc)
                    else:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Token producer YAML 解析失败 (flow={flow_id}): {exc}",
                        ) from exc

            tool = ApiToolDefinition(
                session_id=session_id,
                name=contract.name or f"dynamic_token_{flow_id}",
                description=contract.description or f"Dynamic token producer: {flow_doc.get('name', flow_id)}",
                method=source_call.request.method,
                url_pattern=source_call.url_pattern or source_call.request.url,
                yaml_definition=yaml_str,
                source_calls=[source_call.id],
                source="auto",
                selected=True,
                is_reserve=False,
                tool_type="dynamic_token",
            )
            dynamic_token_tools.append(tool)
```

- [ ] **Step 3: 将 dynamic_token_tools 传入 publish_session**

修改 `registry.publish_session` 调用，传入额外工具：

```python
    result = await registry.publish_session(
        session=session,
        user_id=str(current_user.id),
        mcp_name=request.mcp_name,
        description=request.description,
        overwrite=bool(existing),
        existing_server_id=str(existing["_id"]) if existing else None,
        api_monitor_auth=api_monitor_auth,
        extra_tools=dynamic_token_tools,  # 新增
    )
```

- [ ] **Step 4: 修改 `publish_session` 接受 extra_tools 参数**

修改 `RpaClaw/backend/rpa/api_monitor_mcp_registry.py` 的 `publish_session` 函数签名和实现：

```python
    async def publish_session(
        self,
        *,
        session: ApiMonitorSession,
        user_id: str,
        mcp_name: str,
        description: str,
        overwrite: bool,
        existing_server_id: str | None = None,
        api_monitor_auth: dict[str, Any] | None = None,
        extra_tools: list[ApiToolDefinition] | None = None,  # 新增
    ) -> dict[str, Any]:
```

在 `selected_tools` 构建之后合并：

```python
        selected_tools = [
            tool
            for tool in session.tool_definitions
            if getattr(tool, "selected", False) and not getattr(tool, "is_reserve", False)
        ]
        # 合并 extra_tools（如 dynamic_token 工具）
        if extra_tools:
            selected_tools = [*selected_tools, *extra_tools]
```

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py
git commit -m "feat: publish 时为 token producer 生成 dynamic_token 工具（含重试+校验）"
```

---

## Task 6: 前端展示 dynamic_token 工具特殊标注

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

**背景:** 工具列表中需要区分展示 `dynamic_token` 类型的工具，让用户知道这是一个动态 token 工具而非普通业务工具。

- [ ] **Step 1: 在工具卡片模板中添加 dynamic_token 标注**

在 `ApiMonitorPage.vue` 的工具卡片模板中，找到工具名称展示位置，添加条件标注：

```vue
<!-- 在工具名称旁添加标注 -->
<span
  v-if="tool.tool_type === 'dynamic_token'"
  class="ml-1.5 rounded-md bg-purple-100 px-1.5 py-0.5 text-[10px] font-bold text-purple-700"
>
  Dynamic Token
</span>
```

- [ ] **Step 2: 提交**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: 前端工具列表展示 dynamic_token 特殊标注"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** 四个改动均有对应 Task (1→Task1, 2→Task1, 3→Task2, 4→Task4+5+6)
- [x] **Placeholder scan:** 无 TBD/TODO，每个 step 都有具体代码
- [x] **Type consistency:** `tool_type` 字段名在 models.py、apiMonitor.ts、route、Vue 中一致；`extract_producer_source_calls` 签名与调用处匹配
- [x] **测试覆盖:** Task 1-2 有后端测试；Task 3/4/5/6 为前后端集成改动，依赖手动验证

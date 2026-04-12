# AI 录制助手 Snapshot V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 录制助手引入统一的 `snapshot_v2`、真实 Playwright locator bundle 和局部二次展开机制，提升复杂页面操作与数据提取的命中率。

**Architecture:** 保留现有 `assistant.py -> assistant_runtime.py -> step/generator` 主链路，但把 AI 侧页面抽象从简化元素列表升级为三层 `snapshot_v2`。`actionable_nodes` 复用手动录制已 vendored 的 locator 语义，`content_nodes` 服务提取类指令，`containers` 作为复杂页面的局部收敛锚点；当第一轮解析不确定时，再对目标容器做局部二次展开。

**Tech Stack:** Python 3.13, Playwright async API, unittest, vendored Playwright locator runtime, FastAPI backend RPA assistant runtime.

---

## File Structure

### Existing files to modify

- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py`
  - 负责页面快照构建、collection 检测、结构化意图解析和结构化动作执行。
  - 本次需要升级为 `snapshot_v2` 生产者，新增局部二次展开和不确定性判断。
- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant.py`
  - 负责 AI 提示词、消息格式化、chat/reason-act 执行链。
  - 本次需要改为消费 `snapshot_v2`，并把局部展开的诊断信息回传给模型和 step。
- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`
  - 负责 assistant/assistant_runtime 的现有单元测试。
  - 本次新增 `snapshot_v2`、局部展开、复杂页面解析与提取回归。

### New files to create

- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_snapshot_runtime.py`
  - 负责浏览器侧 `snapshot_v2` JS 注入脚本常量与最小 Python 适配。
  - 目标是把大块 JS 从 `assistant_runtime.py` 拆出来，降低 runtime 文件复杂度。
- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_resolution.py`
  - 负责节点打分、不确定性判断、容器选择和局部二次展开结果合并。
  - 避免把 `assistant_runtime.py` 继续做成巨型文件。

### Files intentionally not changed in first phase

- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\generator.py`
  - 第一阶段只要求 AI step 产出更真实的 locator/candidates；生成器不需要新增动作语义。
- `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\manager.py`
  - 手动录制 locator runtime 已经存在，本计划只复用其语义，不重构 manager。

## Task 1: 搭建 Snapshot V2 的测试骨架

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`
- Test: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定 `snapshot_v2` 顶层结构**

```python
    async def test_build_page_snapshot_v2_includes_actionable_content_and_containers(self):
        frame = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[],
        )
        page = _FakeSnapshotPage(frame)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "act-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "ContractList20260411124156",
                            "action_kinds": ["click"],
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                            "locator_candidates": [{"kind": "role", "selected": True, "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"}}],
                            "validation": {"status": "ok"},
                            "bbox": {"x": 10, "y": 20, "width": 120, "height": 24},
                            "center_point": {"x": 70, "y": 32},
                            "is_visible": True,
                            "is_enabled": True,
                            "hit_test_ok": True,
                            "element_snapshot": {"tag": "a", "text": "ContractList20260411124156"},
                        }
                    ],
                    "content_nodes": [
                        {
                            "node_id": "content-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "semantic_kind": "cell",
                            "text": "已归档",
                            "bbox": {"x": 300, "y": 20, "width": 80, "height": 24},
                            "locator": {"method": "text", "value": "已归档"},
                            "element_snapshot": {"tag": "td", "text": "已归档"},
                        }
                    ],
                    "containers": [
                        {
                            "container_id": "table-1",
                            "frame_path": [],
                            "container_kind": "table",
                            "name": "合同列表",
                            "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                            "summary": "合同下载列表",
                            "child_actionable_ids": ["act-1"],
                            "child_content_ids": ["content-1"],
                        }
                    ],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(
                page,
                frame_path_builder=lambda current: current._frame_path,
            )

        self.assertIn("actionable_nodes", snapshot)
        self.assertIn("content_nodes", snapshot)
        self.assertIn("containers", snapshot)
        self.assertEqual(snapshot["actionable_nodes"][0]["locator"]["method"], "role")
        self.assertEqual(snapshot["content_nodes"][0]["semantic_kind"], "cell")
        self.assertEqual(snapshot["containers"][0]["container_kind"], "table")
```

- [ ] **Step 2: 运行单测确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantFrameAwareSnapshotTests.test_build_page_snapshot_v2_includes_actionable_content_and_containers`

Expected: FAIL，提示 `build_page_snapshot()` 返回结构里没有 `actionable_nodes/content_nodes/containers` 或 `_extract_frame_snapshot_v2` 不存在。

- [ ] **Step 3: 只补最小实现入口，让测试进入更深失败**

```python
async def _extract_frame_snapshot_v2(frame) -> Dict[str, Any]:
    return {
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
    }


async def build_page_snapshot(page, frame_path_builder: Callable[[Any], Any]) -> Dict[str, Any]:
    frames: List[Dict[str, Any]] = []
    actionable_nodes: List[Dict[str, Any]] = []
    content_nodes: List[Dict[str, Any]] = []
    containers: List[Dict[str, Any]] = []

    async def walk(frame) -> None:
        frame_path = await _resolve_frame_path(frame, frame_path_builder)
        snapshot_v2 = await _extract_frame_snapshot_v2(frame)
        frames.append(
            {
                "frame_path": frame_path,
                "url": getattr(frame, "url", ""),
                "frame_hint": "main document" if not frame_path else " -> ".join(frame_path),
                "elements": snapshot_v2.get("actionable_nodes", []),
                "collections": [],
            }
        )
        actionable_nodes.extend(snapshot_v2.get("actionable_nodes", []))
        content_nodes.extend(snapshot_v2.get("content_nodes", []))
        containers.extend(snapshot_v2.get("containers", []))

    await walk(page.main_frame)
    return {
        "url": page.url,
        "title": await page.title(),
        "frames": frames,
        "actionable_nodes": actionable_nodes,
        "content_nodes": content_nodes,
        "containers": containers,
    }
```

- [ ] **Step 4: 重跑目标测试确认通过**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantFrameAwareSnapshotTests.test_build_page_snapshot_v2_includes_actionable_content_and_containers`

Expected: PASS

- [ ] **Step 5: 提交最小骨架**

```bash
git add RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/rpa/assistant_runtime.py
git commit -m "test: add snapshot v2 skeleton coverage"
```

## Task 2: 把浏览器侧快照升级为 actionable/content/container 三层结构

**Files:**
- Create: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_snapshot_runtime.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py`
- Test: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定可操作节点的空间与命中信息**

```python
    async def test_build_page_snapshot_v2_keeps_bbox_and_hit_test_signals(self):
        frame = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[],
        )
        page = _FakeSnapshotPage(frame)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "download-link",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "下载文档",
                            "action_kinds": ["click"],
                            "locator": {"method": "css", "value": "a.link-special"},
                            "locator_candidates": [{"kind": "css", "selected": True, "locator": {"method": "css", "value": "a.link-special"}}],
                            "validation": {"status": "ok"},
                            "bbox": {"x": 100, "y": 200, "width": 88, "height": 18},
                            "center_point": {"x": 144, "y": 209},
                            "is_visible": True,
                            "is_enabled": True,
                            "hit_test_ok": True,
                            "element_snapshot": {"tag": "a", "title": "下载文档"},
                        }
                    ],
                    "content_nodes": [],
                    "containers": [],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(page, frame_path_builder=lambda current: current._frame_path)

        node = snapshot["actionable_nodes"][0]
        self.assertEqual(node["bbox"]["width"], 88)
        self.assertEqual(node["center_point"]["y"], 209)
        self.assertTrue(node["hit_test_ok"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantFrameAwareSnapshotTests.test_build_page_snapshot_v2_keeps_bbox_and_hit_test_signals`

Expected: FAIL，字段缺失或未正确透传。

- [ ] **Step 3: 抽出浏览器侧 snapshot runtime 常量并实现字段透传**

```python
# assistant_snapshot_runtime.py
SNAPSHOT_V2_JS = r"""() => {
  return JSON.stringify({
    actionable_nodes: [],
    content_nodes: [],
    containers: [],
  });
}"""


# assistant_runtime.py
from .assistant_snapshot_runtime import SNAPSHOT_V2_JS


async def _extract_frame_snapshot_v2(frame) -> Dict[str, Any]:
    raw = await frame.evaluate(SNAPSHOT_V2_JS)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        return {"actionable_nodes": [], "content_nodes": [], "containers": []}
    return {
        "actionable_nodes": list(data.get("actionable_nodes") or []),
        "content_nodes": list(data.get("content_nodes") or []),
        "containers": list(data.get("containers") or []),
    }
```

- [ ] **Step 4: 重跑目标测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantFrameAwareSnapshotTests.test_build_page_snapshot_v2_keeps_bbox_and_hit_test_signals`

Expected: PASS

- [ ] **Step 5: 提交 snapshot runtime 拆分**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "refactor: add snapshot v2 runtime module"
```

## Task 3: 用真实 locator bundle 替换 AI 侧简化 locator 语义

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定 AI step 使用真实 locator candidate**

```python
    async def test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [
                        {"kind": "role", "selected": False, "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"}},
                        {"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}},
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertEqual(resolved["resolved"]["locator_candidates"][1]["selected"], True)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node`

Expected: FAIL，当前代码仍会走 `_build_locator_candidates_for_element()` 并丢失真实 bundle。

- [ ] **Step 3: 最小实现，优先复用 snapshot 上的 locator 数据**

```python
def _node_locator_bundle(node: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    locator_candidates = list(node.get("locator_candidates") or [])
    locator = node.get("locator")
    if locator_candidates and locator:
        selected_kind = ""
        for candidate in locator_candidates:
            if candidate.get("selected"):
                selected_kind = str(candidate.get("kind") or "")
                break
        return locator, locator_candidates, selected_kind or str(locator_candidates[0].get("kind") or "")
    locator_candidates = _build_locator_candidates_for_element(node)
    locator = _candidate_locator_payload(locator_candidates)
    return locator, locator_candidates, str(locator_candidates[0].get("kind") or "")
```

- [ ] **Step 4: 重跑目标测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node`

Expected: PASS

- [ ] **Step 5: 提交 locator 语义统一**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "fix: reuse snapshot locator bundles for ai steps"
```

## Task 4: 新增 content_nodes 解析路径，优先服务 extract_text

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定 `extract_text` 优先命中 content node**

```python
    async def test_resolve_structured_intent_extract_text_prefers_content_nodes(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "button-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "role": "button",
                    "name": "复制标题",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "复制标题"},
                    "locator_candidates": [{"kind": "role", "selected": True, "locator": {"method": "role", "role": "button", "name": "复制标题"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [
                {
                    "node_id": "title-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "semantic_kind": "heading",
                    "role": "heading",
                    "text": "Quarterly Report",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "element_snapshot": {"tag": "h2", "text": "Quarterly Report"},
                }
            ],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "report title"},
                "result_key": "report_title",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertEqual(resolved["resolved"]["content_node"]["semantic_kind"], "heading")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_extract_text_prefers_content_nodes`

Expected: FAIL，当前逻辑只遍历 `frames[].elements`。

- [ ] **Step 3: 最小实现 content node 解析与诊断回填**

```python
def _resolve_content_node(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    expected_name = _normalize_hint((intent.get("target_hint") or {}).get("name"))
    for node in snapshot.get("content_nodes", []):
        haystack = " ".join(
            [
                str(node.get("text") or ""),
                str(node.get("semantic_kind") or ""),
                str(node.get("role") or ""),
            ]
        ).lower()
        if expected_name and expected_name not in haystack:
            continue
        return node
    return None
```

- [ ] **Step 4: 重跑目标测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_extract_text_prefers_content_nodes`

Expected: PASS

- [ ] **Step 5: 提交 content_nodes 通路**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: prefer content nodes for ai extract actions"
```

## Task 5: 实现不确定性判断与局部二次展开解析

**Files:**
- Create: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_resolution.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定复杂容器下触发局部展开**

```python
    async def test_resolve_structured_intent_uses_local_expansion_when_top_candidates_are_close(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "下载一",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "下载一"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "下载一"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
                {
                    "node_id": "download-2",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "下载二",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "下载二"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "下载二"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
            ],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["download-1", "download-2"],
                    "child_content_ids": [],
                }
            ],
        }

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "expand_container_snapshot",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "download-1-row-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "ContractList20260411124156",
                            "row_index": 1,
                            "action_kinds": ["click"],
                            "locator": {"method": "text", "value": "ContractList20260411124156"},
                            "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}}],
                            "validation": {"status": "ok"},
                            "hit_test_ok": True,
                        }
                    ],
                    "content_nodes": [],
                }
            ),
        ):
            resolved = ASSISTANT_MODULE.resolve_structured_intent(
                snapshot,
                {
                    "action": "click",
                    "description": "点击第一个文件下载",
                    "prompt": "点击第一个文件下载",
                    "target_hint": {"role": "link", "name": "file download"},
                    "ordinal": "first",
                },
            )

        self.assertTrue(resolved["resolved"]["assistant_diagnostics"]["used_local_expansion"])
        self.assertEqual(resolved["resolved"]["locator"]["value"], "ContractList20260411124156")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_uses_local_expansion_when_top_candidates_are_close`

Expected: FAIL，当前没有 `expand_container_snapshot()` 和不确定性判定。

- [ ] **Step 3: 新建 resolution 模块，放入最小打分与触发规则**

```python
# assistant_resolution.py
def should_expand_locally(
    candidates: List[Dict[str, Any]],
    containers_by_id: Dict[str, Dict[str, Any]],
    intent: Dict[str, Any],
) -> bool:
    if len(candidates) < 2:
        return False
    score_gap = float(candidates[0].get("score", 0)) - float(candidates[1].get("score", 0))
    container = containers_by_id.get(str(candidates[0].get("container_id") or ""))
    prompt = " ".join(str(intent.get(key) or "") for key in ("prompt", "description")).lower()
    has_relative_terms = any(token in prompt for token in ["第一个", "最后一个", "这行", "右边", "左边", "first", "last"])
    complex_container = bool(container and container.get("container_kind") in {"table", "grid", "list", "tree", "card_group", "toolbar", "form_section"})
    return score_gap < 1.5 or (complex_container and has_relative_terms)
```

- [ ] **Step 4: 在 runtime 中接入局部展开并重跑测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_resolve_structured_intent_uses_local_expansion_when_top_candidates_are_close`

Expected: PASS

- [ ] **Step 5: 提交局部展开骨架**

```bash
git add RpaClaw/backend/rpa/assistant_resolution.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add local container expansion for ambiguous ai actions"
```

## Task 6: 在 assistant 提示词和观察文本里暴露 snapshot v2 / diagnostics

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant.py`
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定 observation 展示容器和节点摘要**

```python
    async def test_react_agent_build_observation_lists_snapshot_v2_containers(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["a-1", "a-2"],
                    "child_content_ids": ["c-1", "c-2"],
                }
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Container: table 合同列表", content)
        self.assertIn("actionable=2", content)
        self.assertIn("content=2", content)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAReActAgentTests.test_react_agent_build_observation_lists_snapshot_v2_containers`

Expected: FAIL，当前 observation 只列 `frames/elements/collections`。

- [ ] **Step 3: 最小改造观察文本与消息格式**

```python
def _snapshot_frame_lines(snapshot: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for container in snapshot.get("containers", []):
        lines.append(
            "Container: "
            f"{container.get('container_kind', 'container')} "
            f"{container.get('name', '')} "
            f"(actionable={len(container.get('child_actionable_ids') or [])}, "
            f"content={len(container.get('child_content_ids') or [])})"
        )
    if lines:
        lines.append("")
    ...
```

- [ ] **Step 4: 重跑目标测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAReActAgentTests.test_react_agent_build_observation_lists_snapshot_v2_containers`

Expected: PASS

- [ ] **Step 5: 提交提示词/观察改造**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "refactor: expose snapshot v2 summaries to ai prompts"
```

## Task 7: 回归验证 click/fill/extract_text 三类 AI 指令

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\tests\test_rpa_assistant.py`

- [ ] **Step 1: 写失败测试，固定复杂表格里的 `click first file`**

```python
    async def test_execute_structured_click_records_locator_from_local_expansion(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个文件下载",
            "prompt": "点击第一个文件下载",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "text", "value": "ContractList20260411124156"},
                "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}}],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": "first",
                "selected_locator_kind": "text",
                "assistant_diagnostics": {"used_local_expansion": True, "container_id": "table-1"},
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "text:ContractList20260411124156")
        self.assertTrue(result["step"]["assistant_diagnostics"]["used_local_expansion"])
```

- [ ] **Step 2: 运行该测试确认失败**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant.RPAAssistantStructuredExecutionTests.test_execute_structured_click_records_locator_from_local_expansion`

Expected: FAIL，`assistant_diagnostics` 未透传或定位方式不对。

- [ ] **Step 3: 补最小实现并重跑**

```python
    step = {
        "action": action,
        "source": "ai",
        "target": json.dumps(step_target, ensure_ascii=False),
        "frame_path": frame_path,
        "locator_candidates": resolved.get("locator_candidates", []),
        "validation": {"status": "ok", "details": "assistant structured action"},
        "collection_hint": resolved.get("collection_hint", {}),
        "item_hint": resolved.get("item_hint", {}),
        "ordinal": resolved.get("ordinal"),
        "assistant_diagnostics": {
            **(resolved.get("assistant_diagnostics", {}) or {}),
            "resolved_frame_path": frame_path,
            "selected_locator_kind": resolved.get("selected_locator_kind", ""),
            "collection_kind": resolved.get("collection_hint", {}).get("kind", ""),
        },
        ...
    }
```

- [ ] **Step 4: 运行 assistant 全量测试**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant`

Expected: PASS

- [ ] **Step 5: 提交整体验证**

```bash
git add RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/rpa/assistant_resolution.py RpaClaw/backend/rpa/assistant_snapshot_runtime.py
git commit -m "feat: add snapshot v2 for ai command targeting"
```

## Task 8: 最终验证与文档同步

**Files:**
- Modify: `D:\code\MyScienceClaw\.worktrees\fix-ai-command\docs\superpowers\plans\2026-04-12-ai-command-snapshot-v2.md`

- [ ] **Step 1: 运行最终验证命令**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_assistant`

Expected: PASS

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator`

Expected: PASS，确保 AI 侧改动没有破坏已有录制/生成路径。

Run: `python -m py_compile D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant.py D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_runtime.py D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_resolution.py D:\code\MyScienceClaw\.worktrees\fix-ai-command\RpaClaw\backend\rpa\assistant_snapshot_runtime.py`

Expected: no output

- [ ] **Step 2: 自检计划完成状态并更新备注**

```markdown
- [x] Snapshot v2 top-level structure landed
- [x] Locator bundle reuse landed
- [x] Content node resolution landed
- [x] Local expansion landed
- [x] Assistant diagnostics landed
```

- [ ] **Step 3: 最终提交**

```bash
git add docs/superpowers/plans/2026-04-12-ai-command-snapshot-v2.md
git commit -m "docs: finalize ai command snapshot v2 plan"
```

## Self-Review

### Spec coverage

- `snapshot_v2` 三层结构：Task 1, Task 2
- locator 真值统一：Task 3
- `content_nodes` 服务数据提取：Task 4
- 局部二次展开：Task 5
- AI 提示与诊断透传：Task 6, Task 7
- 回归与验收：Task 8

### Placeholder scan

- 未使用 `TODO/TBD/implement later`
- 每个任务都给出具体文件、命令和最小代码骨架
- 没有“参考前一任务”这类跨任务省略

### Type consistency

- 顶层统一使用 `snapshot["actionable_nodes"] / snapshot["content_nodes"] / snapshot["containers"]`
- 局部展开接口统一命名为 `expand_container_snapshot`
- 诊断字段统一挂在 `resolved["assistant_diagnostics"]` 和 `step["assistant_diagnostics"]`

Plan complete and saved to `docs/superpowers/plans/2026-04-12-ai-command-snapshot-v2.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

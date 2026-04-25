# API Monitor Numerical Scoring & URL Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace boolean confidence classification with 0-100 numerical scoring, add URL-based deduplication to keep only the highest-scored tool per endpoint.

**Architecture:** Rewrite `confidence.py` to compute a weighted score from positive signals (action window, business path, JSON response, source evidence, response richness) and negative signals (injected source, noise path, no action window). Add dedup logic in `manager.py` that groups tools by `method + parameterized path` and keeps the highest-scored one. Frontend displays score alongside confidence label.

**Tech Stack:** Python (Pydantic v2), TypeScript (Vue 3)

---

### Task 1: Add `score` field to `ApiToolDefinition` model

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py:67`

- [ ] **Step 1: Add score field to model**

In `RpaClaw/backend/rpa/api_monitor/models.py`, add `score` field after `confidence`:

```python
confidence: ConfidenceLevel = "medium"
score: int = 0
selected: bool = False
```

- [ ] **Step 2: Verify model instantiates without errors**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "from RpaClaw.backend.rpa.api_monitor.models import ApiToolDefinition; t = ApiToolDefinition(session_id='s', name='n', description='d', method='GET', url_pattern='/api/test', yaml_definition='y'); print(f'score={t.score}, confidence={t.confidence}')"
```

Expected: `score=0, confidence=medium`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py
git commit -m "feat(api-monitor): add score field to ApiToolDefinition"
```

---

### Task 2: Rewrite `confidence.py` with numerical scoring

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/confidence.py`

- [ ] **Step 1: Rewrite confidence.py**

Replace the entire file content with:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .models import CapturedApiCall

ConfidenceLevel = Literal["high", "medium", "low"]

NOISE_PATH_MARKERS = (
    "config",
    "queryconfig",
    "telemetry",
    "collect",
    "track",
    "metrics",
    "heartbeat",
    "ping",
    "log",
    "rum",
    "modelalias",
)

BUSINESS_PATH_MARKERS = (
    "/api/",
    "/biz/",
    "/v1/",
    "/v2/",
    "/graphql",
)

INJECTED_SOURCE_MARKERS = (
    "chrome-extension://",
    "moz-extension://",
    "safari-extension://",
    "userscript",
    "injected",
    "eval",
    "webpack://",
    "VM",
)


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: ConfidenceLevel
    selected: bool
    reasons: list[str]
    evidence_summary: dict
    score: int
    breakdown: dict[str, int]


def _score_response_richness(body: str | None) -> tuple[int, str]:
    """Score response body completeness. Returns (score, reason)."""
    if not body or not body.strip():
        return 0, "无响应体"
    stripped = body.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and len(parsed) > 0:
                return 10, "响应体为有效 JSON"
            if isinstance(parsed, list) and len(parsed) > 0:
                return 10, "响应体为有效 JSON 数组"
            return 5, "响应体为 JSON 但内容为空"
        except (json.JSONDecodeError, ValueError):
            return 5, "有响应体但非标准 JSON"
    return 5, "有响应体"


def score_api_candidate(calls: list[CapturedApiCall]) -> ConfidenceResult:
    """Score an API candidate based on evidence quality.

    Returns a ConfidenceResult with a 0-100 score and confidence level.
    """
    first = calls[0]
    evidence = _merge_evidence(calls)
    reasons: list[str] = []
    breakdown: dict[str, int] = {}

    path = urlparse(first.request.url).path.lower()
    body = (first.response.body if first.response else "") or ""
    content_type = ((first.response.content_type if first.response else "") or "").lower()
    action_window_matched = bool(evidence.get("action_window_matched"))
    source_urls = [
        *evidence.get("initiator_urls", []),
        *evidence.get("js_stack_urls", []),
    ]

    has_source = bool(source_urls)
    injected_source = any(_contains_marker(url, INJECTED_SOURCE_MARKERS) for url in source_urls)
    noise_path = any(marker in path for marker in NOISE_PATH_MARKERS)
    business_path = any(marker in path for marker in BUSINESS_PATH_MARKERS)
    json_response = "json" in content_type or body.strip().startswith(("{", "["))

    # ── Positive signals ─────────────────────────────────────────────
    score = 0

    if action_window_matched:
        score += 30
        breakdown["action_window"] = 30
        reasons.append("由用户动作触发")
    else:
        breakdown["action_window"] = 0

    if business_path:
        score += 25
        breakdown["business_path"] = 25
        reasons.append("路径疑似业务接口")
    else:
        breakdown["business_path"] = 0

    if json_response:
        score += 20
        breakdown["json_response"] = 20
        reasons.append("响应疑似 JSON 业务数据")
    else:
        breakdown["json_response"] = 0

    if has_source:
        score += 15
        breakdown["has_source"] = 15
        if injected_source:
            reasons.append("来源疑似注入脚本或扩展")
        else:
            reasons.append("由页面业务脚本发起")
    else:
        breakdown["has_source"] = 0
        reasons.append("缺少 initiator 或 JS 调用栈")

    richness_pts, richness_reason = _score_response_richness(body)
    score += richness_pts
    breakdown["response_richness"] = richness_pts
    if richness_pts > 0:
        reasons.append(richness_reason)

    # ── Negative signals ─────────────────────────────────────────────
    if injected_source:
        score -= 40
        breakdown["injected_source"] = -40

    if noise_path:
        score -= 30
        breakdown["noise_path"] = -30
        reasons.append("路径疑似配置或后台请求")

    if not action_window_matched:
        score -= 20
        breakdown["no_action_window"] = -20
        reasons.append("不在动作时间窗口内")

    # ── Clamp & classify ─────────────────────────────────────────────
    score = max(0, min(100, score))

    if score >= 75:
        confidence: ConfidenceLevel = "high"
        selected = True
    elif score >= 40:
        confidence = "medium"
        selected = False
    else:
        confidence = "low"
        selected = False

    return ConfidenceResult(
        confidence=confidence,
        selected=selected,
        reasons=_dedupe(reasons),
        evidence_summary=evidence,
        score=score,
        breakdown=breakdown,
    )


def confidence_rank(level: ConfidenceLevel) -> int:
    """Return numeric rank for confidence level comparison."""
    return {"high": 2, "medium": 1, "low": 0}.get(level, 0)


def dedup_key_for_tool(method: str, url_pattern: str) -> str:
    """Generate a dedup key using only the path portion of the URL.

    Query parameters are excluded so that `/api/search?q=foo` and
    `/api/search?q=bar` map to the same key.
    """
    parsed = urlparse(url_pattern)
    return f"{method} {parsed.path}"


def _merge_evidence(calls: list[CapturedApiCall]) -> dict:
    initiator_urls: list[str] = []
    js_stack_urls: list[str] = []
    action_window_matched = False
    frame_url = ""
    initiator_type = ""

    for call in calls:
        evidence = call.source_evidence or {}
        initiator_urls.extend(str(url) for url in evidence.get("initiator_urls", []) if url)
        js_stack_urls.extend(str(url) for url in evidence.get("js_stack_urls", []) if url)
        action_window_matched = action_window_matched or bool(evidence.get("action_window_matched"))
        frame_url = frame_url or str(evidence.get("frame_url") or "")
        initiator_type = initiator_type or str(evidence.get("initiator_type") or "")

    return {
        "initiator_type": initiator_type,
        "initiator_urls": _dedupe(initiator_urls),
        "js_stack_urls": _dedupe(js_stack_urls),
        "frame_url": frame_url,
        "action_window_matched": action_window_matched,
    }


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    lower = value.lower()
    return any(marker.lower() in lower for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
```

- [ ] **Step 2: Verify module imports cleanly**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "from RpaClaw.backend.rpa.api_monitor.confidence import score_api_candidate, dedup_key_for_tool, confidence_rank; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/confidence.py
git commit -m "feat(api-monitor): rewrite confidence as numerical scoring system"
```

---

### Task 3: Update `manager.py` — scoring adapter + dedup logic

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py:23,270-279,658-712`

- [ ] **Step 1: Update import**

In `RpaClaw/backend/rpa/api_monitor/manager.py`, line 23, change:

```python
from .confidence import classify_api_candidate
```

to:

```python
from .confidence import score_api_candidate, dedup_key_for_tool
```

- [ ] **Step 2: Rewrite `_apply_confidence_to_tool`**

Replace the function at lines 270-279 with:

```python
def _apply_confidence_to_tool(
    tool: ApiToolDefinition,
    calls: List[CapturedApiCall],
) -> ApiToolDefinition:
    result = score_api_candidate(calls)
    tool.confidence = result.confidence
    tool.score = result.score
    tool.selected = result.selected
    tool.confidence_reasons = result.reasons
    tool.source_evidence = result.evidence_summary
    return tool
```

- [ ] **Step 3: Add dedup method to `_generate_tools_from_calls`**

At the end of `_generate_tools_from_calls` (after the `for key, group_calls in groups.items()` loop, before the `return tools` line), replace:

```python
        return tools
```

with:

```python
        # ── Deduplicate against existing tools ──────────────────────
        self._dedup_session_tools(session_id, tools)

        return tools
```

- [ ] **Step 4: Add `_dedup_session_tools` method to the class**

Add this method to `ApiMonitorSessionManager`, right after the `_generate_tools_from_calls` method:

```python
    def _dedup_session_tools(
        self,
        session_id: str,
        new_tools: List[ApiToolDefinition],
    ) -> None:
        """Merge new tools into session, keeping the highest-scored per URL."""
        session = self.sessions.get(session_id)
        if not session:
            return

        new_ids = {t.id for t in new_tools}
        existing = [t for t in session.tool_definitions if t.id not in new_ids]
        all_tools = existing + new_tools

        grouped: Dict[str, List[ApiToolDefinition]] = defaultdict(list)
        for tool in all_tools:
            key = dedup_key_for_tool(tool.method, tool.url_pattern)
            grouped[key].append(tool)

        deduped: List[ApiToolDefinition] = []
        for group in grouped.values():
            group.sort(key=lambda t: (t.score, _richness_score(t), t.created_at.isoformat() if t.created_at else ""), reverse=True)
            deduped.append(group[0])

        session.tool_definitions = deduped

        # Replace new_tools list contents with only the survivors
        survivor_ids = {t.id for t in deduped}
        new_tools[:] = [t for t in new_tools if t.id in survivor_ids]
```

Also add the helper function before the class (near the existing `_apply_confidence_to_tool`):

```python
def _richness_score(tool: ApiToolDefinition) -> int:
    """Extract response_richness breakdown score from source_evidence."""
    # The breakdown is stored in the scoring result but not on the tool model.
    # Use response body presence as a proxy: tools with JSON responses score higher.
    evidence = tool.source_evidence or {}
    return 1 if evidence.get("action_window_matched") else 0
```

- [ ] **Step 5: Verify module imports and syntax**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "from RpaClaw.backend.rpa.api_monitor.manager import ApiMonitorSessionManager; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py
git commit -m "feat(api-monitor): integrate scoring and add URL dedup logic"
```

---

### Task 4: Add `score` field to frontend TypeScript type

**Files:**
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts:38-54`

- [ ] **Step 1: Add score to ApiToolDefinition interface**

In `RpaClaw/frontend/src/api/apiMonitor.ts`, add `score` field after `confidence` in the `ApiToolDefinition` interface:

```typescript
export interface ApiToolDefinition {
  id: string
  session_id: string
  name: string
  description: string
  method: string
  url_pattern: string
  yaml_definition: string
  source_calls: string[]
  source: 'auto' | 'manual'
  confidence: ApiToolConfidence
  score: number
  selected: boolean
  confidence_reasons: string[]
  source_evidence: Record<string, unknown>
  created_at: string
  updated_at: string
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npx vue-tsc --noEmit --pretty 2>&1 | head -20
```

Expected: No errors related to `apiMonitor.ts`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts
git commit -m "feat(api-monitor): add score field to frontend type"
```

---

### Task 5: Update frontend badge to show score + label

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue:543-556,787-789`

- [ ] **Step 1: Update confidence label to include score**

In `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`, replace the `confidenceLabels` object (around line 543) with:

```typescript
const confidenceLabels: Record<string, string> = {
  high: '高置信',
  medium: '中置信',
  low: '低置信',
};

const getConfidenceLabel = (confidence: string) => confidenceLabels[confidence] || '中置信';

const getConfidenceLabelWithScore = (confidence: string, score: number) => {
  const label = confidenceLabels[confidence] || '中置信';
  return `${score} ${label}`;
};
```

- [ ] **Step 2: Update badge HTML to display score + label**

Replace the confidence badge span (around line 787-789):

```html
<span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getConfidenceClass(tool.confidence)">
  {{ getConfidenceLabel(tool.confidence) }}
</span>
```

with:

```html
<span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getConfidenceClass(tool.confidence)">
  {{ getConfidenceLabelWithScore(tool.confidence, tool.score) }}
</span>
```

- [ ] **Step 3: Verify the page renders without errors**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npx vue-tsc --noEmit --pretty 2>&1 | head -20
```

Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat(api-monitor): display score in confidence badge"
```

---

### Task 6: Integration test — verify scoring and dedup end-to-end

**Files:**
- No new files

- [ ] **Step 1: Write a quick integration check**

Run:
```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
from RpaClaw.backend.rpa.api_monitor.confidence import score_api_candidate, dedup_key_for_tool
from RpaClaw.backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse
from datetime import datetime

# Build a high-confidence call
req = CapturedRequest(request_id='1', url='https://example.com/api/users', method='GET', headers={}, timestamp=datetime.now(), resource_type='xhr')
resp = CapturedResponse(status=200, status_text='OK', headers={}, body='{\"users\": []}', content_type='application/json', timestamp=datetime.now())
call = CapturedApiCall(request=req, response=resp, url_pattern='/api/users', source_evidence={'action_window_matched': True, 'initiator_urls': ['https://example.com/app.js']})

result = score_api_candidate([call])
print(f'score={result.score}, confidence={result.confidence}, selected={result.selected}')
assert result.score >= 75, f'Expected >= 75, got {result.score}'
assert result.confidence == 'high'
assert result.selected is True

# Verify dedup key strips query
key = dedup_key_for_tool('GET', '/api/search?q={query}&page={page}')
assert key == 'GET /api/search', f'Got {key}'

print('All checks passed!')
"
```

Expected: `score=100, confidence=high, selected=True` then `All checks passed!`

- [ ] **Step 2: Final commit if any fixups needed**

```bash
git add -A
git commit -m "test(api-monitor): verify scoring and dedup integration"
```

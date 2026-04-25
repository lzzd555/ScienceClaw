# API Monitor 数值评分与 URL 去重

## 背景

当前 API Monitor 的置信度是布尔分类（high/medium/low），4 个条件全满足才为 high。不同页面采集时，同一 URL 的接口会反复生成工具定义，无法自动去重。同级别置信度内也无法区分优劣。

## 目标

1. 将置信度从布尔分类改为 0-100 数值评分，映射回 high/medium/low 展示
2. 同一 URL 只保留分数最高的工具定义
3. 前端展示具体分数

## 评分模型

### 加分项（最高 100 分）

| 维度 | 满分 | 判定规则 |
|------|------|----------|
| action_window | 30 | `action_window_matched == true` |
| business_path | 25 | URL 路径含 `/api/`、`/biz/`、`/v1/`、`/v2/`、`/graphql` |
| json_response | 20 | Content-Type 含 `json` 或 body 以 `{`/`[` 开头 |
| has_source | 15 | `initiator_urls` 或 `js_stack_urls` 非空 |
| response_richness | 10 | 无 body → 0 分；有 body 且非空 → 5 分；body 是可解析 JSON 且 keys > 0 → 10 分 |

### 扣分项

| 维度 | 扣分 | 判定规则 |
|------|------|----------|
| injected_source | -40 | 调用方 URL 含 `chrome-extension://`、`webpack://`、`eval` 等注入标记 |
| noise_path | -30 | URL 路径含 `telemetry`、`track`、`metrics`、`heartbeat` 等噪声标记 |
| no_action_window | -20 | `action_window_matched == false`（与加分项互斥） |

### 最终分数

`final_score = clamp(加分合计 + 扣分合计, 0, 100)`

### 置信度映射

- `score >= 75` → `high`，自动选中（`selected = true`）
- `40 <= score < 75` → `medium`
- `score < 40` → `low`

## 去重逻辑

### 去重键

`f"{method} {parameterize_url(path_only)}"`，query 参数不参与。

| 原始 URL | 去重键 |
|----------|--------|
| `/api/users/123?fields=name` | `GET /api/users/{id}` |
| `/api/search?q=foo&page=2` | `GET /api/search` |

### 去重时机

`_generate_tools_from_calls` 中所有工具生成完毕后、返回之前，做一次批量去重。去重范围包括本次新工具 + `session.tool_definitions` 已有工具。

### 去重流程

1. 收集 existing_tools + new_tools，按去重键分组
2. 每组内排序：score 降序 → score 相同则 response_richness 降序 → 仍相同则取最新的
3. 每组只保留排名最高的
4. 用去重后的列表替换 `session.tool_definitions`
5. 返回去重后属于本次新增/更新的工具

前端在 `stop_recording` / `analyze_page` 返回后会刷新工具列表，不需要额外通知机制。

## 数据模型变更

### `ApiToolDefinition`（models.py）

新增字段：
```python
score: int = 0  # 0-100 数值评分
```

`confidence`、`selected`、`confidence_reasons`、`source_evidence` 保留不变，由 score 映射得出。

## 前端变更

### `ApiToolDefinition` 类型（apiMonitor.ts）

新增 `score: number` 字段。

### 置信度 badge 展示（ApiMonitorPage.vue）

从纯文字标签改为 **分数 + 标签**组合，例如 `85 高置信`、`52 中置信`、`18 低置信`。

颜色规则不变：
- 高置信 (≥75)：emerald 绿色
- 中置信 (40-74)：amber 琥珀色
- 低置信 (<40)：slate 灰色

### 不变的部分

- 采纳/取消采纳交互逻辑（基于 `selected`）
- 置信度原因标签展示
- 工具卡片整体布局

## 改动文件清单

### 后端

| 文件 | 改动 |
|------|------|
| `rpa/api_monitor/confidence.py` | 重写为 `score_api_candidate`，返回分数 + 置信度 + 原因；新增 `response_richness` 评分；新增扣分项；新增 `dedup_key_for_tool`（只用 path） |
| `rpa/api_monitor/models.py` | `ApiToolDefinition` 新增 `score: int = 0` |
| `rpa/api_monitor/manager.py` | `_apply_confidence_to_tool` 适配新评分结果；`_generate_tools_from_calls` 末尾增加批量去重 |

### 前端

| 文件 | 改动 |
|------|------|
| `api/apiMonitor.ts` | 类型新增 `score: number` |
| `pages/rpa/ApiMonitorPage.vue` | badge 显示分数 + 标签 |

### 不改动的部分

- `network_capture.py` — 采集逻辑不变
- `llm_analyzer.py` — LLM 工具定义生成不变
- `route/api_monitor.py` — 接口不变，字段自然透传
- MCP 发布流程

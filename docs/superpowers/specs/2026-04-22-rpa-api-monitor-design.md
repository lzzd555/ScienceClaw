# RPA API Monitor 功能设计

> 本文档描述 API Monitor 功能的完整设计方案。该功能允许用户分析网页中可交互元素触发的 API 请求，通过 LLM 自动生成 OpenAI 格式的工具定义，并支持人工审核和补充录制。

## 1. 功能概述

### 1.1 核心流程

```
用户输入 URL → 打开浏览器 → LLM 扫描页面 DOM → 逐个探测可交互元素
    → 捕获触发的 API 请求 → LLM 生成 OpenAI YAML 工具定义
    → 用户审核/编辑/删除 → 用户可补充录制遗漏的 API
```

### 1.2 用户场景

1. **自动分析**：用户输入一个页面 URL，LLM 自动识别页面上的按钮、链接、表单等可交互元素，逐个点击并捕获产生的 API 请求，最终将每个 API 总结为 OpenAI function calling 格式的工具定义
2. **手动录制**：用户手动在浏览器中操作，系统捕获期间的 API 请求并生成工具
3. **工具审核**：用户在页面上查看所有已生成的工具（以 YAML 形式呈现），可编辑或删除
4. **导出**：将所有工具导出为 YAML 文件

### 1.3 页面定位

独立的 Vue 页面，路由为 `/rpa/api-monitor`，不依赖 RPA 录制流程。

## 2. 架构设计

### 2.1 整体架构

```
Frontend                          Backend                              Playwright
ApiMonitorPage.vue                route/api_monitor.py                 Browser Context
  ├─ 浏览器视口 (screencast WS)    ├─ session CRUD                      ├─ page.on("request")
  ├─ 工具列表 (YAML 预览/编辑)     ├─ analyze (SSE 流)                  ├─ page.on("response")
  └─ 控制栏 (分析/录制/导出)       ├─ record start/stop                 └─ CDP screencast
                                   └─ tools CRUD
                                          │
                                   api_monitor/
                                     ├─ manager.py          会话管理+网络监听
                                     ├─ network_capture.py  请求/响应捕获+关联
                                     ├─ llm_analyzer.py     LLM 分析+工具生成
                                     └─ models.py           数据模型
```

### 2.2 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 会话隔离 | 独立的 `ApiMonitorSessionManager` | 与 RPA 录制互不干扰 |
| 网络捕获 | `page.on("request")` + `page.on("response")` | Playwright 原生 API，高层且稳定，无需 JS 注入 |
| LLM 调用 | 复用 `get_llm_model()` + `_stream_llm` 模式 | 与项目现有 AI 基础设施一致 |
| 进度推送 | SSE (Server-Sent Events) | 与 RPA chat 端点模式一致 |
| 浏览器显示 | 复用 CDP screencast WebSocket | 与 RecorderPage 相同模式 |

## 3. 文件结构

### 3.1 新建文件

#### 后端 (6 个文件)

| 文件 | 职责 |
|------|------|
| `backend/rpa/api_monitor/__init__.py` | 包初始化 |
| `backend/rpa/api_monitor/models.py` | Pydantic 数据模型（CapturedRequest、CapturedResponse、CapturedApiCall、ApiToolDefinition、ApiMonitorSession） |
| `backend/rpa/api_monitor/manager.py` | 会话管理器：创建/销毁浏览器上下文、页面导航、网络监听安装、自动分析编排、录制模式控制 |
| `backend/rpa/api_monitor/network_capture.py` | 网络流量捕获引擎：请求/响应关联、静态资源过滤、URL 模式参数化、相似调用去重合并 |
| `backend/rpa/api_monitor/llm_analyzer.py` | LLM 集成：DOM 元素分析（哪些值得探测）、API 调用分析（生成 YAML 工具定义） |
| `backend/route/api_monitor.py` | FastAPI 路由：REST 端点、WebSocket（screencast + events）、SSE（analyze 进度） |

#### 前端 (1 个文件)

| 文件 | 职责 |
|------|------|
| `frontend/src/pages/rpa/ApiMonitorPage.vue` | API Monitor 单页面组件：浏览器视口、工具列表、YAML 编辑、分析/录制控制 |

### 3.2 修改文件

| 文件 | 位置 | 修改内容 |
|------|------|---------|
| `backend/main.py` | ~第 155 行 | 添加 `api_monitor_router` 的 import 和 `include_router` |
| `frontend/src/main.ts` | ~第 99 行 | 在 `/rpa` children 中添加 `{ path: 'api-monitor', component: ApiMonitorPage }` |
| `backend/mongodb/db.py` | `init_indexes()` | 添加 `api_monitor_sessions` 和 `api_monitor_tools` 的索引 |
| `backend/storage/__init__.py` | 集合初始化列表 | 添加新集合名 |

## 4. 数据模型

### 4.1 CapturedRequest

记录单次 HTTP 请求。

```python
class CapturedRequest(BaseModel):
    request_id: str                      # Playwright request 的唯一 ID
    url: str                             # 完整请求 URL
    method: str                          # GET, POST, PUT, DELETE, PATCH
    headers: Dict[str, str]              # 请求头
    body: Optional[str] = None           # POST/PUT body，截断至 10KB
    content_type: Optional[str] = None   # Content-Type
    timestamp: datetime                  # 请求时间
    resource_type: str                   # Playwright resource_type: "xhr" 或 "fetch"
```

### 4.2 CapturedResponse

记录对应的 HTTP 响应。

```python
class CapturedResponse(BaseModel):
    status: int                          # HTTP 状态码
    status_text: str                     # 状态描述
    headers: Dict[str, str]              # 响应头
    body: Optional[str] = None           # 响应体，截断至 50KB
    content_type: Optional[str] = None   # Content-Type
    timestamp: datetime                  # 响应时间
```

### 4.3 CapturedApiCall

请求-响应对，关联触发元素。

```python
class CapturedApiCall(BaseModel):
    id: str
    request: CapturedRequest
    response: Optional[CapturedResponse] = None
    trigger_element: Optional[Dict] = None   # 触发此请求的页面元素定位器
    url_pattern: Optional[str] = None        # 参数化的 URL（如 /api/users/{id}）
    duration_ms: Optional[float] = None      # 请求耗时
```

### 4.4 ApiToolDefinition

LLM 生成的工具定义，以 OpenAI function calling 格式呈现。

```python
class ApiToolDefinition(BaseModel):
    id: str
    session_id: str
    name: str                                 # LLM 生成的函数名（如 get_user_list）
    description: str                          # LLM 生成的描述
    method: str                               # HTTP 方法
    url_pattern: str                          # 参数化 URL（如 /api/users/{id}）
    headers_schema: Optional[Dict] = None     # 请求头 schema
    request_body_schema: Optional[Dict] = None # 请求体 schema
    response_body_schema: Optional[Dict] = None # 响应体 schema
    trigger_locator: Optional[Dict] = None    # 触发元素的 Playwright 定位器
    yaml_definition: str                      # 完整的 OpenAI 格式 YAML 字符串
    source_calls: List[str] = []              # 关联的 CapturedApiCall ID 列表
    source: str = "auto"                      # "auto"（LLM 分析）或 "manual"（用户录制）
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### 4.5 ApiMonitorSession

监控会话，管理浏览器上下文和所有捕获数据。

```python
class ApiMonitorSession(BaseModel):
    id: str
    user_id: str
    sandbox_session_id: str
    status: str = "idle"                      # idle, analyzing, recording, stopped
    target_url: Optional[str] = None
    captured_calls: List[CapturedApiCall] = []
    tool_definitions: List[ApiToolDefinition] = []
    active_tab_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

## 5. API 端点

所有端点前缀：`/api/v1/api-monitor/`

### 5.1 会话管理

| Method | Path | 描述 |
|--------|------|------|
| POST | `/session/start` | 创建监控会话，打开浏览器，导航到指定 URL。Body: `{url: string}` |
| GET | `/session/{id}` | 获取会话状态，包含工具定义列表 |
| POST | `/session/{id}/stop` | 停止会话，关闭浏览器上下文 |
| POST | `/session/{id}/navigate` | 导航到新 URL。Body: `{url: string}` |

### 5.2 浏览器控制

| Method | Path | 描述 |
|--------|------|------|
| GET | `/session/{id}/tabs` | 列出当前浏览器所有标签页 |
| POST | `/session/{id}/tabs/{tab_id}/activate` | 切换到指定标签页 |
| WebSocket | `/screencast/{id}` | CDP screencast 视频流（复用 RPA 的 screencast 模式） |

### 5.3 分析与录制

| Method | Path | 描述 |
|--------|------|------|
| **POST** | **`/session/{id}/analyze`** | **启动自动分析。返回 SSE 流，推送分析进度** |
| POST | `/session/{id}/record/start` | 开始手动录制模式 |
| POST | `/session/{id}/record/stop` | 停止录制，LLM 处理新捕获的 API 请求并生成工具 |

#### analyze SSE 事件类型

| 事件 | 数据 | 描述 |
|------|------|------|
| `analysis_started` | `{url}` | 分析开始 |
| `elements_found` | `{count, elements[]}` | 扫描到 N 个可交互元素 |
| `probing_element` | `{index, total, element}` | 正在探测第 N 个元素 |
| `calls_captured` | `{count, new_calls[]}` | 捕获到新 API 请求 |
| `generating_tools` | `{endpoint_count}` | 正在发送给 LLM 生成工具 |
| `tool_generated` | `{tool: ApiToolDefinition}` | 新工具生成完毕 |
| `analysis_complete` | `{total_tools, total_calls}` | 分析完成 |
| `error` | `{message}` | 出现错误 |

### 5.4 工具管理

| Method | Path | 描述 |
|--------|------|------|
| GET | `/session/{id}/tools` | 列出所有工具定义 |
| PUT | `/session/{id}/tools/{tool_id}` | 更新工具定义。Body: `{yaml_definition: string}` |
| DELETE | `/session/{id}/tools/{tool_id}` | 删除工具定义 |
| POST | `/session/{id}/export` | 导出所有工具为 YAML 文档，返回文件内容 |

### 5.5 实时事件

| Method | Path | 描述 |
|--------|------|------|
| WebSocket | `/session/{id}/events` | 实时事件推送（录制中的新 API 捕获、状态变化） |

## 6. 前端页面设计

### 6.1 布局

```
+----------------------------------------------------------+
|  [API Monitor]  [ URL 输入框 ]  [Go] [分析] [录制] [导出] [返回] |
+----------------------------------------------------------+
|                          |                                |
|   浏览器视口              |   工具定义列表                   |
|   (Screencast Canvas)    |   +--------------------------+ |
|                          |   | GET  /api/users  [编辑][删除]|
|                          |   |   yaml preview (折叠)     | |
|                          |   +--------------------------+ |
|   [标签页栏]              |   | POST /api/users [编辑][删除]|
|   [地址栏]               |   |   yaml 展开的 Monaco 编辑器| |
|                          |   +--------------------------+ |
|                          |                                |
+----------------------------------------------------------+
|  状态栏: 12 个工具 | 分析: 空闲 | 录制: 关闭                   |
+----------------------------------------------------------+
```

### 6.2 组件结构

`ApiMonitorPage.vue` 为单文件组件（参照 `RecorderPage.vue` 模式），内部包含：

- **顶部控制栏**：URL 输入、导航按钮、分析/录制/导出控制
- **左侧浏览器视口**：canvas 元素 + screencast WebSocket 连接（复用 RecorderPage 的 WS 模式）
- **右侧工具面板**：
  - 工具卡片列表，每个卡片显示：HTTP 方法徽章（GET 绿/POST 蓝/PUT 橙/DELETE 红）、URL 模式、描述
  - 点击展开 Monaco 编辑器显示 YAML 内容
  - 编辑/删除按钮
- **底部状态栏**：工具数量、分析状态、录制状态

### 6.3 关键交互流程

**自动分析流程**：
1. 用户输入 URL，点击 Go → POST `/session/start` → 创建会话
2. 浏览器视口显示页面内容
3. 用户点击"分析" → POST `/session/{id}/analyze` → SSE 连接
4. 前端接收 SSE 事件，实时更新进度条和工具列表
5. 分析完成后，工具列表显示所有发现的 API 工具

**手动录制流程**：
1. 用户点击"录制"按钮（变为红色激活状态）
2. 用户在浏览器视口中正常操作
3. 用户再次点击"录制"停止 → 后端处理新捕获的请求
4. 新工具出现在列表中，标记为 `source: "manual"`

**编辑工具**：
1. 点击工具卡片的"编辑"按钮
2. Monaco 编辑器展开，显示完整 YAML
3. 用户修改后点击保存 → PUT `/session/{id}/tools/{tool_id}`

## 7. 核心实现细节

### 7.1 网络捕获机制

**请求/响应关联**：
- `page.on("request")` 触发时，将 request 对象存入 `in_flight_requests` 字典，key 为 `id(request)`
- `page.on("response")` 触发时，通过 `response.request` 获取原始 request 对象，从字典中取出并创建 `CapturedApiCall`

**过滤规则**（`should_capture_request`）：
- 只捕获 `resource_type` 为 `"xhr"` 或 `"fetch"` 的请求
- 跳过 URL 以 `.js`、`.css`、`.png`、`.jpg`、`.woff`、`.ico` 等结尾的静态资源
- 跳过 `data:` URI
- 跳过 WebSocket 升级请求（`ws://`、`wss://`）

**响应体获取**：
- 使用 `response.text()` 获取，设置 50KB 截断和 5s 超时
- 失败时静默忽略（不影响页面行为）

### 7.2 自动分析流程

```
1. 注入 JS 扫描页面 DOM
   → 收集所有可交互元素（button, a, input, select, [role="button"] 等）
   → 返回元素的定位器列表

2. LLM 分析元素列表
   → 输入：元素列表（tag, role, text, locator）
   → 输出：分类结果（safe_to_probe / skip_dangerous / skip_navigation）
   → 跳过含 "delete", "remove", "logout", "sign out" 等危险文本的元素
   → 跳过导航到其他域名的链接

3. 逐个探测安全元素
   for each element:
     a. 清空当前捕获队列
     b. locator.click()（带 5s 超时）
     c. 等待 2s 收集 API 请求
     d. 记录捕获结果，关联触发元素
     e. 恢复页面状态（go back 如果发生了导航，关闭弹窗等）
     → SSE 推送 probing_element + calls_captured 事件

4. 分组去重
   → 按参数化 URL 模式分组（如 /api/users/1 和 /api/users/2 合并）
   → 合并同组的请求/响应 schema

5. LLM 生成工具定义
   for each group:
     → 输入：一组 API 调用样本（最多 5 个）
     → 输出：OpenAI function calling YAML
     → 验证 YAML 结构，失败则重试
     → SSE 推送 tool_generated 事件
```

### 7.3 URL 模式参数化

将具体 URL 转换为带参数的模板：

| 输入 | 输出 | 规则 |
|------|------|------|
| `/api/users/123` | `/api/users/{id}` | 纯数字路径段 → `{id}` |
| `/api/users/abc-def-456-789` | `/api/users/{id}` | UUID-like 段 → `{id}` |
| `/api/search?q=foo&page=2` | `/api/search?q={query}&page={page}` | query 参数值 → `{param_name}` |
| `/api/posts/2024/01/15` | `/api/posts/{year}/{month}/{day}` | 日期格式路径段 |

### 7.4 LLM 工具生成 Prompt 设计

LLM 接收的输入：
```json
{
  "endpoint": "POST /api/users",
  "samples": [
    {
      "request_body": {"name": "Alice", "email": "alice@example.com"},
      "response_status": 201,
      "response_body": {"id": 1, "name": "Alice", "email": "alice@example.com", "created_at": "..."}
    }
  ],
  "page_context": "User management page with a form to create new users"
}
```

LLM 输出的 YAML 格式：
```yaml
name: create_user
description: "Create a new user account"
method: POST
url: /api/users
parameters:
  type: object
  properties:
    name:
      type: string
      description: "User's full name"
      in: body
    email:
      type: string
      description: "User's email address"
      in: body
  required:
    - name
    - email
response:
  type: object
  properties:
    id:
      type: integer
    name:
      type: string
    email:
      type: string
    created_at:
      type: string
      format: date-time
```

## 8. 关键复用点

| 复用内容 | 来源文件 | 使用位置 |
|----------|---------|---------|
| CDP 连接器 | `rpa/cdp_connector.py` `get_cdp_connector()` | `api_monitor/manager.py` 创建浏览器 |
| 浏览器上下文创建 | `rpa/manager.py:141-159` `create_session()` | `api_monitor/manager.py` |
| 页面事件绑定模式 | `rpa/manager.py:501-572` `_bind_page()` | `api_monitor/manager.py` |
| Screencast WebSocket | `route/rpa.py` screencast 端点 | `route/api_monitor.py` |
| SSE 流式响应 | `route/rpa.py` chat SSE 端点 | `route/api_monitor.py` analyze |
| WebSocket 认证 | `route/rpa.py` `_get_ws_user()` | `route/api_monitor.py` |
| HTTP 认证 | `route/rpa.py` `Depends(get_current_user)` | `route/api_monitor.py` |
| LLM 流式调用 | `rpa/assistant.py:984-1008` `_stream_llm()` | `api_monitor/llm_analyzer.py` |
| `get_llm_model()` | `deepagent/engine.py` | `api_monitor/llm_analyzer.py` |
| Monaco 编辑器组件 | `frontend/src/components/` | `ApiMonitorPage.vue` YAML 编辑 |
| Screencast canvas 模式 | `RecorderPage.vue:340-400` | `ApiMonitorPage.vue` |

## 9. 实现阶段

### Phase 1：基础框架
1. 创建 `backend/rpa/api_monitor/` 包和 `models.py`
2. 创建 `manager.py` 基础会话生命周期（创建/导航/停止）
3. 创建 `route/api_monitor.py`（session CRUD + screencast WS）
4. 修改 `main.py`、`main.ts`、`db.py` 注册路由和索引
5. 创建 `ApiMonitorPage.vue` 骨架（浏览器视口 + 空面板）

**验收标准**：访问 `/rpa/api-monitor`，输入 URL，浏览器视口显示页面内容。

### Phase 2：网络捕获
6. 创建 `network_capture.py`（请求/响应关联、过滤、URL 模式提取）
7. Manager 中添加 `install_network_listeners()`
8. 添加 record start/stop 端点
9. 添加 events WebSocket 实时推送
10. 前端显示捕获的 API 调用列表

**验收标准**：开启录制模式，在浏览器中操作，API 请求列表实时更新。

### Phase 3：自动分析
11. 创建 `llm_analyzer.py`（DOM 分析 prompt + 工具生成 prompt）
12. Manager 中实现 `analyze_page()`（元素扫描→逐个探测→LLM 生成）
13. 添加 `/analyze` SSE 端点
14. 前端实现分析进度显示 + 工具列表实时更新

**验收标准**：点击"分析"按钮，SSE 进度实时显示，工具列表逐步填充。

### Phase 4：工具管理
15. 实现工具的 CRUD 端点
16. 前端 YAML 预览和 Monaco 编辑器
17. 实现导出功能（下载 YAML 文件）
18. 手动录制模式 UI（Record 开关）

**验收标准**：编辑 YAML 保存成功，删除工具列表更新，导出文件正确。

### Phase 5：完善
19. 分页 API 检测和合并
20. 认证 header 标记为 credential 引用
21. 动态 URL 模式优化
22. 错误处理和恢复

## 10. 技术挑战与应对

| 挑战 | 应对方案 |
|------|---------|
| 请求/响应关联 | 用 `id(request)` 做 key，`response.request` 反查。Playwright 保证同一请求对象引用 |
| 获取响应体不干扰页面 | `response.text()` 加超时和截断，失败静默忽略 |
| 盲点元素导致导航/破坏 | LLM 先分类元素安全性，跳过 delete/logout 等危险操作。点击后检测导航并回退 |
| LLM 上下文窗口不足 | 按 URL 模式分组后，每组最多送 5 个样本给 LLM |
| 过滤静态资源 | 按 `resource_type` 过滤（只保留 xhr/fetch）+ URL 后缀黑名单 |
| 与 RPA 会话隔离 | 独立的 Manager 和 `sandbox_session_id`，通过 CDPConnector 的 session key 天然隔离 |
| SPA 客户端路由 | 监听 `framenavigated` + JS 注入监听 `popstate`/`pushState` |
| YAML 格式校验 | 后端解析 LLM 输出的 YAML，验证必需字段（name, description, parameters），失败重试 |

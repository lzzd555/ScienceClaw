# RPA API Monitor UI Enhancement Design

> 基于已有的 `2026-04-22-rpa-api-monitor-design.md` 设计文档，本文档描述前端 UI 实现方案，包含 API Monitor 新页面和现有页面增强。

## 1. 实现范围

| 范围 | 说明 |
|------|------|
| **新建** `ApiMonitorPage.vue` | API Monitor 页面（`/rpa/api-monitor`），浏览器视口 + 终端日志 + 工具列表 |
| **增强** `ToolsPage.vue` | MCP 工具 Tab 改为 Bento Grid 卡片风格 |
| **增强** `McpToolEditorPage.vue` | 改为 Split Layout（左 Metadata + 右 YAML 编辑器） |
| **整合** 导航 | 页面间导航链接（Tools ↔ API Monitor ↔ Editor） |
| **后端** `api_monitor/` 模块 | 按原设计文档实现，本文档不再重复 |

## 2. UI 设计规范

### 2.1 配色体系（Material Design 3 Dark Theme）

基于三个 HTML 原型提取的统一配色，已定义为 Tailwind 自定义色：

| Token | Hex | 用途 |
|-------|-----|------|
| `background` / `surface` | `#0b1326` | 主背景 |
| `surface-container` | `#171f33` | 卡片内背景 |
| `surface-container-high` | `#222a3d` | 高亮容器 |
| `surface-container-lowest` | `#060e20` | 编辑器背景 |
| `primary` | `#57f1db` | 主强调色（按钮、链接、活跃状态） |
| `primary-container` | `#2dd4bf` | 次强调色 |
| `error` | `#ffb4ab` | 错误/录制状态 |
| `on-surface` | `#dae2fd` | 主文本 |
| `on-surface-variant` | `#bacac5` | 次文本 |
| `outline-variant` | `#3c4a46` | 边框 |
| `secondary` | `#7bd0ff` | 终端日志 RECV |
| `tertiary` | `#ead0ff` | YAML 属性键 |

### 2.2 字体

| 用途 | Font | Size | Weight |
|------|------|------|--------|
| h1 | Inter | 32px | 700 |
| h2 | Inter | 24px | 600 |
| body-md | Inter | 14px | 400 |
| label-caps | Inter | 12px | 600 (0.05em spacing) |
| code-block | JetBrains Mono | 13px | 400 |

### 2.3 间距与圆角

- 间距：xs=4px, sm=8px, md=16px, lg=24px, xl=48px
- 圆角：DEFAULT=2px, lg=4px, xl=8px, full=12px

## 3. ApiMonitorPage.vue 设计

### 3.1 路由

```typescript
// frontend/src/main.ts — 在 /rpa children 中添加
{
  path: 'api-monitor',
  component: () => import('./pages/rpa/ApiMonitorPage.vue')
}
```

### 3.2 页面布局

```
+----------------------------------------------------------+
|  顶部控制栏                                                |
|  [← 返回]  API Monitor  [URL 输入框] [Go] [分析] [录制] [导出] |
+----------------------------------------------------------+
| 左 50%                    | 右 50%                         |
|                           |                                |
|  浏览器视口                |  ┌─ 终端日志面板 ─────────────┐ |
|  (Screencast Canvas)      |  │ [14:32] POST /api/users    │ |
|                           |  │ [14:32] HTTP 200 (142ms)   │ |
|  - canvas + WS 连接       |  │ [14:32] BUILD YAML...      │ |
|  - 鼠标/键盘事件转发       |  └───────────────────────────┘ |
|  - 标签页栏               |                                |
|                           |  ┌─ 工具列表 ─────────────────┐ |
|                           |  │ ┌─────────────────────────┐│ |
|                           |  │ │ GET  /api/users          ││ |
|                           |  │ │ List all users [编辑][删] ││ |
|                           |  │ └─────────────────────────┘│ |
|                           |  │ ┌─────────────────────────┐│ |
|                           |  │ │ POST /api/users          ││ |
|                           |  │ │ Create user  [编辑][删]   ││ |
|                           |  │ └─────────────────────────┘│ |
|                           |  └───────────────────────────┘ |
+----------------------------------------------------------+
|  状态栏: 12 tools | Idle | Not Recording                  |
+----------------------------------------------------------+
```

### 3.3 组件分解

`ApiMonitorPage.vue` 为单文件组件（参照 `RecorderPage.vue` 模式），内部子组件：

| 区域 | 实现方式 |
|------|---------|
| 顶部控制栏 | template 内联 |
| 浏览器视口 | 复用 `RecorderPage.vue:340-400` 的 Screencast Canvas + WS 模式 |
| 终端日志 | 自定义终端组件，接收 SSE/WS 事件追加日志行 |
| 工具卡片 | template 循环渲染，点击展开 Monaco YAML 编辑器 |
| 状态栏 | template 内联 |

### 3.4 录制按钮

参照 Recording 原型的设计：
- 大红色圆形按钮，带脉冲动画（scale 叠加圆环）
- 下方显示录制时长
- 状态切换：idle 灰色 → recording 红色脉冲

### 3.5 分析进度

参照 Recording 原型的 3 步进度：
1. **Network Listening**（完成态 ✓）— 监听流量中
2. **Analyzing API Shape**（活跃态，带进度条）— 检测端点和请求结构
3. **Generating Specification**（等待态）— 生成 YAML 工具定义

进度通过 SSE 事件驱动更新。

### 3.6 终端日志

参照 Recording 原型的终端组件：
- 仿 macOS 窗口标题栏（三个圆点 + 文件名）
- 等宽字体 JetBrains Mono
- 彩色日志级别：INFO(primary)、RECV(secondary)、ANALYZE(surface-tint)、BUILD(primary)、ERROR(error)
- 自动滚动到底部
- 清空 + 复制按钮

### 3.7 工具卡片

每个工具卡片显示：
- HTTP 方法徽章（GET 绿 / POST 蓝 / PUT 橙 / DELETE 红）
- URL 模式（参数化后的路径）
- LLM 生成的描述
- 来源标签：`auto` 或 `manual`
- 展开后显示 Monaco YAML 编辑器
- 保存 → PUT `/session/{id}/tools/{tool_id}`

### 3.8 关键交互状态机

```
                    ┌─────────────────────┐
                    │       idle          │
                    └──┬──────┬──────┬────┘
                       │      │      │
          (点击分析)    │      │      │ (点击导出)
                       ▼      │      │
                 ┌─────────┐  │      ▼
                 │analyzing│  │   export
                 └────┬────┘  │   (下载 YAML)
                      │       │
          (SSE complete)      │
                      ▼       │
                    idle      │
                              │
              (点击录制)       │
                              ▼
                       ┌──────────┐
                       │recording │
                       └────┬─────┘
                            │
               (点击停止录制) │
                            ▼
                       ┌───────────┐
                       │processing │
                       └────┬──────┘
                            │
               (LLM 处理完成)│
                            ▼
                          idle
```

## 4. ToolsPage.vue 增强

### 4.1 改动范围

只修改 MCP 工具 Tab 的展示方式。Custom Code Tools Tab 不变。

### 4.2 Bento Grid 卡片

将 MCP 工具列表从当前的表格/列表改为 3 列网格卡片：

```
┌──────────────────────────────────────────────┐
│ Tool Inventory                    [Grid][List][Filter] │
│ Manage MCP tools. Total: 12 active.              │
├──────────────┬──────────────┬──────────────────┤
│ ┌──────────┐ │ ┌──────────┐ │ ┌──────────────┐ │
│ │ 🔍 Google│ │ │ 🌤 Weather│ │ │ 🗄 PostgreSQL│ │
│ │ Search   │ │ │ API      │ │ │ DB           │ │
│ │ v2.1.4   │ │ │ v1.0.2   │ │ │ v3.5.0       │ │
│ │ ●Active  │ │ │ ○Inactive│ │ │ ●Active      │ │
│ │          │ │ │          │ │ │              │ │
│ │ [Edit]   │ │ │ [Edit]   │ │ │ [Edit]       │ │
│ │ [Delete] │ │ │ [Delete] │ │ │ [Delete]     │ │
│ └──────────┘ │ └──────────┘ │ └──────────────┘ │
└──────────────┴──────────────┴──────────────────┘
```

**卡片元素**（按原型）：
- 顶部装饰线（Active 工具 `bg-gradient-to-r from-primary to-transparent`）
- 图标 + 名称 + 版本
- 状态徽章：Active(绿)、Inactive(灰)、Error(红)
- 描述文本
- Error 状态额外显示错误消息
- 底部 Edit / Delete 按钮

### 4.3 "Add Tool" 增强

现有 "Add Tool" 按钮增加下拉：
- **Manual** — 跳转到 `/chat/tools/mcp/new`
- **From API Monitor** — 跳转到 `/rpa/api-monitor`

## 5. McpToolEditorPage.vue 增强

### 5.1 Split Layout

将现有编辑器改为原型中的分栏布局：

**左 1/3 — Metadata 面板**：
- Tool Name（input）
- Description（textarea）
- Tags（带添加/删除的 tag 列表）
- Authentication 分区：
  - Auth Type（select：API Key / Bearer Token / OAuth 2.0 / None）
  - API Key（password input + 显示/隐藏）

**右 2/3 — YAML 编辑器**：
- 仿 VS Code 编辑器外壳：
  - 标签栏：OpenAI Spec (YAML) / JSON Schema 切换
  - 工具栏：Format + Copy 按钮
  - 行号 + 语法高亮（复用现有 Monaco 编辑器）
  - 深色背景 `#0B1120`

**底部操作栏**：
- Discard Changes（边框按钮）
- Save Changes（primary 填充按钮）

### 5.2 实现方式

在现有 `McpToolEditorPage.vue` 基础上重构 template，保持现有 API 调用逻辑不变，只改变 UI 布局和样式。

## 6. 后端 API Monitor 模块（概要）

完全按 `2026-04-22-rpa-api-monitor-design.md` 实现，不做改动。关键文件：

| 文件 | 职责 |
|------|------|
| `backend/rpa/api_monitor/__init__.py` | 包初始化 |
| `backend/rpa/api_monitor/models.py` | Pydantic 数据模型 |
| `backend/rpa/api_monitor/manager.py` | 会话管理 + 网络监听 + 自动分析编排 |
| `backend/rpa/api_monitor/network_capture.py` | 请求/响应捕获 + 过滤 + URL 模式化 + 去重 |
| `backend/rpa/api_monitor/llm_analyzer.py` | LLM DOM 分析 + YAML 工具生成 |
| `backend/route/api_monitor.py` | FastAPI 路由 |

修改文件：
- `backend/main.py` — 注册 `api_monitor_router`
- `backend/mongodb/db.py` — 添加索引
- `frontend/src/main.ts` — 添加路由

## 7. 实现阶段

### Phase 1：后端基础框架 + 前端骨架
1. 创建 `backend/rpa/api_monitor/` 包和 `models.py`
2. 创建 `manager.py` 基础会话生命周期（创建/导航/停止）
3. 创建 `route/api_monitor.py`（session CRUD + screencast WS）
4. 修改 `main.py`、`main.ts`、`db.py` 注册路由和索引
5. 创建 `ApiMonitorPage.vue` 骨架（浏览器视口 + 空面板）

### Phase 2：网络捕获 + 录制模式
6. 创建 `network_capture.py`（请求/响应关联、过滤、URL 模式提取）
7. Manager 中添加 `install_network_listeners()`
8. 添加 record start/stop 端点
9. 添加 events WebSocket 实时推送
10. 前端终端日志 + 录制按钮 UI

### Phase 3：自动分析
11. 创建 `llm_analyzer.py`（DOM 分析 prompt + 工具生成 prompt）
12. Manager 中实现 `analyze_page()`
13. 添加 `/analyze` SSE 端点
14. 前端分析进度步骤 + SSE 事件处理

### Phase 4：工具管理 UI
15. 前端工具卡片列表 + YAML 展开/编辑
16. 工具 CRUD 端点联调
17. 导出功能（下载 YAML 文件）
18. 增强现有 ToolsPage 卡片风格
19. 增强现有 McpToolEditorPage Split Layout

### Phase 5：整合完善
20. 导航链接整合（Tools ↔ API Monitor ↔ Editor）
21. "Add Tool" 下拉菜单
22. 错误处理和恢复
23. 分页检测和合并优化

## 8. 关键复用点

| 复用内容 | 来源 | 使用位置 |
|----------|------|---------|
| Screencast Canvas + WS | `RecorderPage.vue` | `ApiMonitorPage.vue` 浏览器视口 |
| CDP Connector | `rpa/cdp_connector.py` | `api_monitor/manager.py` |
| SessionScreencastController | `rpa/screencast.py` | `route/api_monitor.py` screencast WS |
| WS 认证 | `route/rpa.py` `_get_ws_user()` | `route/api_monitor.py` |
| SSE 流式响应 | `route/rpa.py` chat SSE | `route/api_monitor.py` analyze |
| LLM 流式调用 | `deepagent/engine.py` | `api_monitor/llm_analyzer.py` |
| Monaco 编辑器 | `McpToolEditorPage.vue` | `ApiMonitorPage.vue` YAML 编辑 |
| 配色和组件风格 | 三个 HTML 原型的 Tailwind 配置 | 所有增强页面 |

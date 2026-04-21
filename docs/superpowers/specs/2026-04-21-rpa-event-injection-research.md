# RPA 事件注入与处理机制研究

> 本文档记录 RPA 系统中 Playwright 事件注入、捕获、桥接和处理的完整链路，以及基于此机制监听 REST API 请求的理论可行性分析。

## 1. 事件注入架构总览

RPA 录制系统通过三层协作实现浏览器事件的捕获与处理：

```
┌─────────────────────────────────────────────────────────┐
│  浏览器进程 (V8 Engine)                                   │
│                                                         │
│  用户操作 → DOM 事件 → addEventListener 捕获              │
│       ↓                                                 │
│  JS 处理: retarget + locator + emit                      │
│       ↓                                                 │
│  window.__rpa_emit(JSON)  ─── CDP WebSocket ──┐         │
└─────────────────────────────────────────────────┼───────┘
                                                  │
                                                  ▼
┌─────────────────────────────────────────────────────────┐
│  Python 进程 (Playwright Server)                         │
│                                                         │
│  rpa_emit(source, event_json) ← expose_binding 回调      │
│       ↓                                                 │
│  _handle_event(session_id, evt)                          │
│       ↓                                                 │
│  存储 RPAStep + WebSocket 广播                           │
└─────────────────────────────────────────────────────────┘
```

## 2. 浏览器端：JavaScript 注入

### 2.1 注入方式

通过 `context.add_init_script()` 在每个页面加载时自动注入三个 JS 脚本：

```python
# manager.py:488-491
await context.expose_binding("__rpa_emit", rpa_emit, handle=False)
await context.add_init_script(path=str(PLAYWRIGHT_RECORDER_RUNTIME_PATH))   # runtime.js (~305KB)
await context.add_init_script(path=str(PLAYWRIGHT_RECORDER_ACTIONS_PATH))    # actions.js
await context.add_init_script(script=CAPTURE_JS)                             # capture.js
```

注入顺序很重要：runtime 必须先加载，因为 actions 和 capture 依赖 `window.__rpaPlaywrightRecorder` 对象。

### 2.2 三个 JS 脚本的职责

| 脚本 | 文件路径 | 职责 |
|------|---------|------|
| **runtime** | `vendor/playwright_recorder_runtime.js` | Playwright 官方选择器算法，提供 `buildLocatorBundle()`、`getRole()`、`getAccessibleName()` 等函数 |
| **actions** | `vendor/playwright_recorder_actions.js` | 安装 DOM 事件监听器，将原始事件映射为 Playwright 动作类型 |
| **capture** | `vendor/playwright_recorder_capture.js` | 编排层：调用 actions 安装监听，调用 runtime 生成定位器，通过 `emit()` 发送事件 |

### 2.3 DOM 事件监听

`playwright_recorder_actions.js` 通过 `document.addEventListener(type, handler, true)` 在**捕获阶段**监听以下 DOM 事件：

| 监听事件 | 映射的 Playwright 动作 | 说明 |
|----------|----------------------|------|
| `click` | `click` / `check` / `uncheck` | 根据元素类型区分：普通元素→click，复选框/单选→check/uncheck |
| `input` | `fill` / `select` / `set_input_files` | 文本输入→fill，下拉选择→select，文件上传→set_input_files |
| `change` | `check` / `uncheck` / `select` | 复选框切换、下拉选择 |
| `keydown` | `press` | 键盘按键，过滤修饰键（Shift/Ctrl/Alt/Meta） |
| `focusin` | — | 记录当前活动目标元素 (activeTarget) |
| `focusout` | — | 清除活动目标 |

关键过滤逻辑：
- **`!event.isTrusted` 检查**：只处理真实用户操作，忽略程序触发的事件（如 `el.click()`）
- **250ms 去重窗口**（`SUPPRESSION_WINDOW_MS`）：相同目标的重复动作在 250ms 内被抑制
- **`isPaused()` 检查**：支持暂停录制

### 2.4 元素重定向（Retarget）

不是所有被点击的元素都是"交互元素"。`retarget()` 函数将事件目标向上查找，定位到最近的交互祖先：

```javascript
function retarget(el) {
    if (['INPUT', 'TEXTAREA', 'SELECT'].indexOf(el.tagName) >= 0) return el;
    if (el.isContentEditable) return el;
    var cur = el;
    while (cur && cur !== document.body) {
        if (INTERACTIVE.indexOf(cur.tagName) >= 0) return cur;     // BUTTON, A, SELECT, TEXTAREA
        if (INTERACTIVE_ROLES.indexOf(cur.getAttribute('role')) >= 0) return cur;
        cur = cur.parentElement;
    }
    return el;
}
```

### 2.5 定位器生成

`buildLocatorBundle(target)` 调用 Playwright runtime 的选择器算法，按优先级生成候选定位器：

1. `get_by_role("button", name="Login")` — ARIA 角色 + 可访问名称
2. `get_by_test_id("submit-btn")` — `data-testid` 属性
3. `get_by_label("Username")` — label 关联
4. `get_by_placeholder("Enter name")` — placeholder 属性
5. `get_by_alt_text("Logo")` — alt 文本
6. `get_by_title("Tooltip")` — title 属性
7. `page.locator("#submit")` — CSS 选择器兜底

GUID-like 的 ID（如 `abc123-def456-...`）会被自动排除，因为它们通常是框架自动生成的，不稳定。

### 2.6 事件发射

`capture.js` 中的 `emit()` 函数将事件序列化为 JSON 并通过 `window.__rpa_emit()` 发送：

```javascript
function emit(evt) {
    evt.timestamp = Date.now();
    evt.sequence = ++_eventSequence;
    evt.url = location.href;
    evt.frame_path = getFramePath();
    window.__rpa_emit(JSON.stringify(evt));
}
```

## 3. 桥接层：expose_binding

### 3.1 工作原理

`context.expose_binding("__rpa_emit", rpa_emit, handle=False)` 是 Playwright 提供的 CDP (Chrome DevTools Protocol) 能力：

1. Playwright 在浏览器 V8 引擎中注册 `window.__rpa_emit` 为一个 **native binding**
2. 当 JS 调用 `window.__rpa_emit(json)` 时，Playwright 通过 CDP WebSocket 将调用参数从浏览器进程**跨进程传递**到 Python 进程
3. Python 端的 `rpa_emit(source, event_json)` 回调被触发

`handle=False` 参数表示 Playwright 不会在浏览器端处理这个调用的返回值（纯单向通知）。

### 3.2 回调处理

```python
# manager.py:465-486
async def rpa_emit(source, event_json: str):
    try:
        evt = json.loads(event_json)
        source_page = _binding_source_get(source, "page")
        source_frame = _binding_source_get(source, "frame")

        # 解析 tab_id
        resolved_tab_id = self._page_tab_ids.get(session_id, {}).get(id(source_page))
        if resolved_tab_id:
            evt.setdefault("tab_id", resolved_tab_id)

        # 构建 frame_path（iframe 支持）
        if source_frame:
            evt["frame_path"] = await self._build_frame_path(source_frame)

        await self._handle_event(session_id, evt)
    except Exception as e:
        logger.error(f"[RPA] binding emit error: {e}")
```

`source` 参数由 Playwright 自动填充，包含：
- `source.page` — 事件来源的 Page 对象
- `source.frame` — 事件来源的 Frame 对象

## 4. Python 端：事件处理

### 4.1 事件来源

`_handle_event` 接收的事件不只来自 JS 注入，还有三个其他来源：

| 来源 | 触发方式 | 事件类型 |
|------|---------|---------|
| **JS 注入** (主要) | `expose_binding` 回调 | click, fill, press, select 等 |
| **页面导航** | `page.on("framenavigated")` | navigate |
| **文件下载** | `page.on("download")` | download |
| **弹窗** | `page.on("popup")` | navigate (新 tab) |

### 4.2 _handle_event 核心逻辑

```python
# manager.py:1002-
async def _handle_event(self, session_id: str, evt: dict):
    # 1. 状态校验
    if session.status != "recording" or session.paused:
        return

    # 2. Tab 切换检测
    if event_tab_id != session.active_tab_id:
        await self.activate_tab(session_id, event_tab_id, source="event")

    # 3. 导航事件特殊处理
    if evt.get("action") == "navigate":
        # 查找前驱步骤，判断是否为 click 导致的导航
        # 如果前驱是 click 且 5s 内，将导航合并为 click 的信号
        ...

    # 4. 创建 RPAStep 存储
    # 5. WebSocket 广播给前端
```

### 4.3 事件数据结构

每个录制的步骤存储为 `RPAStep`：

```python
class RPAStep(BaseModel):
    action: str                    # "click", "fill", "press", "navigate" 等
    target: str                    # JSON 序列化的定位器
    locator_candidates: List[Dict] # 备选定位器列表
    validation: Dict               # 定位器匹配状态和质量
    value: Optional[str]           # 输入值（密码字段遮掩为 {{credential}}）
    description: str               # 人类可读描述
    frame_path: List[str]          # iframe 路径
    tab_id: str                    # 所属浏览器标签页
    sequence: Optional[int]        # 事件序列号
    event_timestamp_ms: Optional[int]  # 事件时间戳
    signals: Dict                  # 附带信号（弹窗、下载等）
```

## 5. 完整事件流示例

以"用户点击登录按钮"为例：

```
1. 用户点击 <button id="login">登录</button>
     ↓
2. 浏览器触发 DOM click 事件
     ↓
3. addEventListener('click', handler, true) 捕获阶段拦截 (actions.js)
     ↓
4. retarget(el) → 确认目标是 BUTTON 元素
     ↓
5. emitLogicalAction('click', target, {})
     ↓
6. shouldSuppress() → 250ms 内无重复，允许通过
     ↓
7. emitAction('click', el, {})
     ↓
8. buildLocatorBundle(el) → 生成定位器候选列表
   - primary: { role: 'button', name: '登录' }
   - candidates: [{ css: '#login' }, { text: '登录' }]
     ↓
9. emit(evt) → window.__rpa_emit(JSON.stringify(evt))
     ↓
10. Playwright CDP 桥 → 跨进程传递到 Python
     ↓
11. rpa_emit(source, event_json) → json.loads + 解析 tab/frame
     ↓
12. _handle_event(session_id, evt) → 创建 RPAStep + WebSocket 广播
     ↓
13. 前端实时显示新录制的步骤
```

## 6. 扩展研究：监听 REST API 请求的可行性

基于 `expose_binding` 机制，理论上完全可以构建一个 REST API 请求/响应监听系统。有以下几种实现方式：

### 6.1 方式一：Playwright 内置事件（推荐）

最简单直接，无需 JS 注入：

```python
page.on("request", lambda req: log(f">> {req.method} {req.url}"))
page.on("response", lambda resp: log(f"<< {resp.status} {resp.url}"))
```

- 优点：零侵入，API 简洁
- 缺点：拿不到响应体（需要额外 `response.text()` 调用）

### 6.2 方式二：page.route() 拦截（最全能）

可以拦截、记录、甚至修改请求和响应：

```python
async def handle_route(route):
    response = await route.fetch()
    body = await response.text()
    log_api(route.request, response, body)
    await route.fulfill(response=response)

await page.route("**/api/**", handle_route)
```

- 优点：请求体+响应体都能拿到，还能 mock/修改
- 缺点：所有匹配的请求都会经过这个 handler，可能影响性能

### 6.3 方式三：expose_binding + JS Hook（与现有系统同构）

复用当前 RPA 的 `expose_binding` 桥梁，在浏览器侧 hook `fetch` 和 `XMLHttpRequest`：

```python
await context.expose_binding("__api_report", api_report, handle=False)
await context.add_init_script(script=API_HOOK_JS)
```

JS 侧 hook fetch 和 XHR：

```javascript
// Hook fetch
const origFetch = window.fetch;
window.fetch = async function(...args) {
    const response = await origFetch.apply(this, args);
    const clone = response.clone();
    clone.text().then(body => {
        window.__api_report(JSON.stringify({
            type: 'fetch', url: args[0], method: args[1]?.method || 'GET',
            status: response.status, body: body.substring(0, 1000),
        }));
    });
    return response;
};

// Hook XMLHttpRequest
const origOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__method = method; this.__url = url;
    return origOpen.call(this, method, url, ...rest);
};
XMLHttpRequest.prototype.send = function(body) {
    this.addEventListener('load', function() {
        window.__api_report(JSON.stringify({
            type: 'xhr', url: this.__url, method: this.__method,
            status: this.status, body: this.responseText.substring(0, 1000),
        }));
    });
    return origSend.call(this, body);
};
```

- 优点：与现有 RPA 架构一致，可以精确控制捕获范围
- 缺点：可能遗漏 Service Worker 发起的请求

### 6.4 方式四：CDP Network 域（最底层）

直接使用 Chrome DevTools Protocol：

```python
cdp = await page.context.new_cdp_session(page)
await cdp.send("Network.enable")
cdp.on("Network.requestWillBeSent", lambda p: ...)
cdp.on("Network.responseReceived", lambda p: ...)
```

- 优点：最完整，包括 WebSocket、Service Worker 等所有网络活动
- 缺点：API 较低层，响应体需要额外 `Network.getResponseBody` 调用

### 6.5 方式对比

| 方式 | 请求体 | 响应体 | 可修改 | 性能影响 | 复杂度 |
|------|--------|--------|--------|---------|--------|
| `page.on()` | 部分 | 需额外调用 | 不能 | 最低 | 低 |
| `page.route()` | 能 | 能 | 能 | 中 | 低 |
| `expose_binding` + JS | 能 | 能 | 能 | 中 | 中 |
| CDP Network | 能 | 需额外调用 | 能 | 低 | 高 |

**推荐**：如果只需要监听，用方式一 (`page.on`)；如果需要拦截或修改，用方式二 (`page.route()`)；如果要和 RPA 录制系统集成，用方式三 (`expose_binding`)。

## 7. 关键文件索引

| 文件 | 职责 |
|------|------|
| `backend/rpa/manager.py:454` | `_ensure_context_recorder()` — 注入入口，注册 binding + 脚本 |
| `backend/rpa/manager.py:465` | `rpa_emit()` — binding 回调，桥接 JS→Python |
| `backend/rpa/manager.py:1002` | `_handle_event()` — 核心事件处理器 |
| `backend/rpa/manager.py:501` | `_bind_page()` — 页面级事件（导航/下载/关闭） |
| `backend/rpa/vendor/playwright_recorder_capture.js` | 事件捕获编排层 |
| `backend/rpa/vendor/playwright_recorder_actions.js` | DOM 事件监听器安装 |
| `backend/rpa/vendor/playwright_recorder_runtime.js` | Playwright 选择器算法 |

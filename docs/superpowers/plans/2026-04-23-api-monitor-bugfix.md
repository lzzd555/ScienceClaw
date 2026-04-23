# API Monitor 前三问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 API Monitor 的 CDP screencast 黑屏、导航超时、按钮失效三个问题

**Architecture:** 核心根因是前端 API 数据提取路径错误 + 后端导航阻塞 HTTP 响应 + get_session 返回协程。修复已完成大部分，只需恢复后台导航

**Tech Stack:** Python/FastAPI (后端), TypeScript/Vue 3 (前端), Playwright/CDP (浏览器)

---

## 根因分析

### 问题一：CDP Screencast 黑屏

**代码追踪：**

```
ApiMonitorPage.vue:296  →  startSession(url)
apiMonitor.ts:81-82     →  apiClient.post('/api-monitor/session/start', { url })
                            return response.data.session   ← 已修复，之前是 response.data.data (undefined)
ApiMonitorPage.vue:297  →  sessionId.value = s.id         ← 之前 s 是 undefined，s.id 抛 TypeError
ApiMonitorPage.vue:300  →  connectScreencast(s.id)         ← 之前从未执行到这里
```

**根因：** `apiMonitor.ts` 的 `startSession()` 读取 `response.data.data`（undefined），因为后端返回的是 `{"status": "success", "session": {...}}`，数据在 `response.data.session`。`s = undefined` → `s.id` 抛 TypeError → `connectScreencast()` 从未被调用 → 黑屏。

**修复状态：** ✅ 已修复 — 所有 7 个 API 函数的数据路径已纠正

### 问题二：导航超时

**代码追踪：**

```
manager.py:147-154       →  浏览器 context + page 创建（~3 秒）
manager.py:164 (原166)   →  await page.goto(url, wait_until="domcontentloaded")  ← 阻塞！
apiClient timeout         →  30 秒（client.ts:11）
PAGE_TIMEOUT_MS           →  60 秒（manager.py:26）
```

**根因：** `page.goto()` 在 `create_session()` 中同步阻塞 HTTP 响应。目标网站加载慢时，前端 axios 30s 超时先于后端 60s 导航超时 → 前端报错 → 后端返回 500。

**修复状态：** ❌ 需要修复 — 导航被我错误移除，需恢复为 `asyncio.create_task` 后台任务

### 问题三：按钮失效

**代码追踪：**

```
ApiMonitorPage.vue:545   →  :disabled="!sessionId || isAnalyzing"     (Analyze)
ApiMonitorPage.vue:553   →  :disabled="!sessionId"                    (Record)
ApiMonitorPage.vue:564   →  :disabled="!sessionId || !tools.length"   (Export)
```

**根因：** 所有按钮依赖 `sessionId`。因问题一，`startSession()` 抛出 TypeError，`sessionId.value` 始终为 `''`，所有按钮始终禁用。

**修复状态：** ✅ 已修复（问题一修后即修）

### 隐藏问题：`get_session()` 返回协程对象

```
manager.py:199 (原204)   →  async def get_session(...)  ← 只是 dict 查找
route/api_monitor.py:96  →  session = api_monitor_manager.get_session(session_id) ← 未 await
                            session 是协程对象，session.user_id → AttributeError
```

**修复状态：** ✅ 已修复 — 改为同步方法 `def get_session(...)`

### 问题四（后续处理）：自动分析提取不全

**根因：** 两层问题：
1. **噪音捕获**：`.glb` 等 3D 资源通过 `fetch()` 加载，`resource_type` 为 `"fetch"`，不在 `STATIC_EXTENSIONS` 中 → 被捕获并生成工具
2. **信号缺失**：GitHub 的 Pull Requests / Fork / Issues 等是 `<a>` 标签跳转，不是 XHR/fetch → `_probe_element` 点击后无 API 调用被捕获

**修复状态：** 🔜 后续处理 — 等前三个 bug 修复验证通过后，作为独立改进任务

---

## 文件结构

| 文件 | 状态 | 修改内容 |
|------|------|----------|
| `backend/rpa/api_monitor/manager.py` | 需修改 | Task 1: 恢复后台导航 |
| `frontend/src/api/apiMonitor.ts` | ✅ 已修复 | 数据提取路径（7 个函数） |
| `frontend/src/pages/rpa/ApiMonitorPage.vue` | ✅ 已修复 | SSE 事件名称对齐 |
| `backend/route/api_monitor.py` | 无需修改 | get_session 同步后已正常 |

---

### Task 1: 恢复 create_session 的后台导航

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py:163-165`

**当前代码（manager.py:163-165）：**
```python
        logger.info("[ApiMonitor] Session %s created, target URL=%s", session_id, target_url)
        return session
```

导航被我错误移除了。需要在 `return session` 之前恢复导航为后台任务。

- [ ] **Step 1: 恢复后台导航**

将 `manager.py` 第 163-165 行（`logger.info` 和 `return session`）替换为：

```python
        # Navigate in background — don't block the HTTP response
        if target_url:
            async def _navigate() -> None:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded")
                    session.target_url = page.url
                    session.updated_at = datetime.now()
                    logger.info("[ApiMonitor] Navigation complete for %s: %s", session_id, page.url)
                except Exception as exc:
                    logger.warning("[ApiMonitor] Navigation failed for %s: %s", session_id, exc)
            asyncio.create_task(_navigate())

        logger.info("[ApiMonitor] Session %s created, target URL=%s", session_id, target_url)
        return session
```

- [ ] **Step 2: 验证无语法错误**

Run: `cd RpaClaw/backend && python -c "from backend.rpa.api_monitor.manager import ApiMonitorSessionManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py
git commit -m "fix: restore background navigation in api_monitor create_session

- Navigation was wrongly removed in previous fix attempt
- Now runs as asyncio.create_task to avoid blocking HTTP response
- Session returns immediately (< 3s), page loads via screencast viewport
- Prevents frontend axios timeout on slow-loading sites"
```

---

## 验证清单

1. **启动服务**
   ```bash
   cd RpaClaw/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000
   cd RpaClaw/frontend && npm run dev
   ```

2. **验证 Session 创建（问题二）**
   - 进入 `/rpa/api-monitor`，输入 URL（如 `https://example.com`），点击 Go
   - **预期：** Session 在 3 秒内创建，终端显示 "Session created: xxx"
   - **不应该：** 等待 30+ 秒然后超时

3. **验证 CDP Screencast（问题一）**
   - Session 创建后
   - **预期：** 左侧 canvas 显示浏览器视口，能看到页面加载过程
   - **不应该：** 黑屏或显示 "Enter a URL" 占位符
   - 终端应显示 "Screencast connected"

4. **验证按钮功能（问题三）**
   - **Analyze 按钮：** 点击后终端显示分析进度，工具卡片出现
   - **Record 按钮：** 点击切换录制状态，REC 指示灯亮
   - **Export 按钮：** 有工具后点击下载 YAML 文件

5. **验证导航后台执行**
   - 使用较慢的 URL（如 GitHub）测试
   - **预期：** Session 立即返回，浏览器在 screencast 中显示加载过程
   - **不应该：** 前端超时报错

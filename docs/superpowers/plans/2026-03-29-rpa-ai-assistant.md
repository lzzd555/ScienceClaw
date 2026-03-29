# RPA AI 录制助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the AI recording assistant chat panel in RecorderPage to accept natural language instructions, generate dynamic Playwright scripts, and execute them in the recording browser in real-time.

**Architecture:** User sends a message via SSE endpoint → backend extracts interactive element tree from recording browser via command file → builds prompt with steps + elements + message → streams LLM response → writes generated code to command file → recording browser executes it (with event capture paused) → result returned via SSE. AI steps stored as `ai_script` type in session.steps.

**Tech Stack:** FastAPI + SSE (sse-starlette), LangChain ChatOpenAI (get_llm_model), Playwright sync API (sandbox), Vue 3 + EventSource (frontend)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/rpa/assistant.py` | Create | RPAAssistant class: LLM prompt building, streaming, command execution, element tree extraction |
| `backend/rpa/manager.py` | Modify | BROWSER_SCRIPT: add command file polling loop + `__rpa_paused` flag. CAPTURE_JS: check paused flag in all handlers. RPAStep model: add `source` and `prompt` fields |
| `backend/rpa/generator.py` | Modify | Handle `ai_script` steps: embed code directly with sync→async conversion |
| `backend/route/rpa.py` | Modify | Add `POST /session/{id}/chat` SSE endpoint + ChatRequest model |
| `frontend/src/pages/rpa/RecorderPage.vue` | Modify | Wire sendMessage to SSE, render streaming responses, show AI steps in step list |

---

### Task 1: Extend RPAStep model and CAPTURE_JS pause mechanism

**Files:**
- Modify: `ScienceClaw/backend/rpa/manager.py:17-27` (RPAStep model)
- Modify: `ScienceClaw/backend/rpa/manager.py:394-436` (CAPTURE_JS event handlers)

- [ ] **Step 1: Add `source` and `prompt` fields to RPAStep**

In `ScienceClaw/backend/rpa/manager.py`, update the RPAStep class at line 17:

```python
class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    value: Optional[str] = None
    screenshot_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    description: Optional[str] = None
    tag: Optional[str] = None
    label: Optional[str] = None
    url: Optional[str] = None
    source: str = "record"  # "record" or "ai"
    prompt: Optional[str] = None  # original user instruction for AI steps
```

- [ ] **Step 2: Add `__rpa_paused` flag to CAPTURE_JS**

In the CAPTURE_JS string inside BROWSER_SCRIPT, add the paused flag initialization right after the IIFE opening `(function() {` (around line 52-53 of the CAPTURE_JS content), before any variable declarations:

```javascript
    window.__rpa_paused = false;
```

Then add a pause check at the top of each event handler. For the click handler at line 394:

```javascript
    document.addEventListener('click', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
```

For the input handler at line 410:

```javascript
    document.addEventListener('input', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
```

For the change handler at line 420:

```javascript
    document.addEventListener('change', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
```

For the keydown handler at line 429:

```javascript
    document.addEventListener('keydown', function(e) {
        if (!e.isTrusted) return;
        if (window.__rpa_paused) return;
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/manager.py
git commit -m "feat(rpa): add source/prompt fields to RPAStep and __rpa_paused flag to CAPTURE_JS"
```

---

### Task 2: Add command file polling to BROWSER_SCRIPT

**Files:**
- Modify: `ScienceClaw/backend/rpa/manager.py:500-509` (BROWSER_SCRIPT main loop)

- [ ] **Step 1: Replace the main while loop with command-aware loop**

Replace the existing main loop in BROWSER_SCRIPT (lines 502-509):

```python
try:
    while True:
        page.wait_for_timeout(1000)
except KeyboardInterrupt:
    browser.close()
    p.stop()
```

With:

```python
import traceback as _tb

def _execute_command(page, cmd_path, result_path):
    \"\"\"Execute a command file and write result.\"\"\"
    try:
        code = open(cmd_path, 'r', encoding='utf-8').read()
        os.remove(cmd_path)
    except Exception as e:
        return

    # Pause event capture during AI script execution
    try:
        page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass

    result = {"success": False, "output": "", "error": None}
    try:
        ns = {"page": page, "os": os, "json": json}
        exec(code, ns)
        if "run" in ns and callable(ns["run"]):
            ret = ns["run"](page)
            result = {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            result = {"success": False, "output": "", "error": "No run(page) function defined"}
    except Exception as e:
        result = {"success": False, "output": "", "error": _tb.format_exc()}

    # Resume event capture
    try:
        page.evaluate("window.__rpa_paused = false")
    except Exception:
        pass

    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result, f)

CMD_PATH = "/tmp/rpa_command.py"
CMD_RESULT_PATH = "/tmp/rpa_command_result.json"

print("READY", flush=True)

try:
    while True:
        if os.path.exists(CMD_PATH):
            _execute_command(page, CMD_PATH, CMD_RESULT_PATH)
        page.wait_for_timeout(500)
except KeyboardInterrupt:
    browser.close()
    p.stop()
```

Note: The existing `print("READY", flush=True)` at line 500 should be removed since it's now included in the new loop block. The new code replaces everything from line 500 to 509.

- [ ] **Step 2: Verify BROWSER_SCRIPT still has correct indentation**

Read the full BROWSER_SCRIPT section to confirm the replacement is syntactically correct Python. The `_execute_command` function and `CMD_PATH`/`CMD_RESULT_PATH` constants must be at the top level of the script (not indented inside any function).

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/manager.py
git commit -m "feat(rpa): add command file polling loop to BROWSER_SCRIPT"
```

---

### Task 3: Create RPAAssistant class

**Files:**
- Create: `ScienceClaw/backend/rpa/assistant.py`

- [ ] **Step 1: Create the assistant module with element extraction and command execution**

Create `ScienceClaw/backend/rpa/assistant.py`:

```python
import json
import logging
import re
import asyncio
import base64
from typing import Dict, List, Any, AsyncGenerator, Optional

import httpx
from backend.deepagent.engine import get_llm_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个 RPA 录制助手。用户正在录制浏览器自动化技能，你需要根据用户的自然语言描述，结合当前页面状态和历史操作，生成 Playwright 同步 API 代码片段。

规则：
1. 生成的代码必须使用 Playwright 同步 API（page.locator().click()，不是 await）
2. 代码必须定义 def run(page): 函数
3. 使用动态适应的选择器：
   - "点击第一个搜索结果" → page.locator("h3").first.click() 或 page.locator("[data-result]").first.click()
   - "获取表格数据" → page.locator("table").first.inner_text()
   - 不要硬编码具体文本内容，用位置/结构/角色选择
4. 操作之间加 page.wait_for_timeout(500) 等待 UI 响应
5. 如果操作可能触发页面导航，在 click 后加 page.wait_for_load_state("load")
6. 用 ```python 代码块包裹代码
7. 代码之外可以附带简短说明"""

# JS to extract interactive elements from the page
EXTRACT_ELEMENTS_JS = """() => {
    const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const els = document.querySelectorAll(INTERACTIVE);
    const results = [];
    let index = 1;
    const seen = new Set();
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (el.disabled) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const name = (el.getAttribute('aria-label') || el.innerText || '').trim().substring(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const href = el.getAttribute('href') || '';
        const value = el.value || '';
        const type = el.getAttribute('type') || '';

        // Dedup by a simple key
        const key = tag + role + name + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name.replace(/\\s+/g, ' ');
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value && tag !== 'input') info.value = value.substring(0, 80);
        if (type) info.type = type;
        const checked = el.checked;
        if (checked !== undefined) info.checked = checked;

        results.push(info);
        index++;
        if (index > 150) break;  // Cap to avoid token explosion
    }
    return JSON.stringify(results);
}"""


class RPAAssistant:
    """AI recording assistant: takes natural language, generates and executes Playwright code."""

    def __init__(self, sandbox_url: str):
        self.sandbox_url = sandbox_url.rstrip("/")
        # Per-session chat histories
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        # Each round = 1 user + 1 assistant message
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        sandbox_session_id: str,
        message: str,
        steps: List[Dict[str, Any]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream AI assistant response. Yields SSE event dicts."""

        # 1. Get page elements
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面..."}}
        elements_json = await self._get_page_elements(sandbox_session_id)

        # 2. Build prompt
        history = self._get_history(session_id)
        messages = self._build_messages(message, steps, elements_json, history)

        # 3. Stream LLM response
        full_response = ""
        async for chunk_text in self._stream_llm(messages):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        # 4. Extract code
        code = self._extract_code(full_response)
        if not code:
            yield {"event": "error", "data": {"message": "未能从 AI 响应中提取到代码"}}
            # Save to history anyway
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": full_response})
            self._trim_history(session_id)
            yield {"event": "done", "data": {}}
            return

        yield {"event": "script", "data": {"code": code}}

        # 5. Execute
        yield {"event": "executing", "data": {}}
        result = await self._execute_command(sandbox_session_id, code)

        if not result["success"]:
            # 6. Retry once with error context
            yield {"event": "message_chunk", "data": {"text": "\n\n执行失败，正在重试..."}}
            retry_messages = messages + [
                {"role": "assistant", "content": full_response},
                {"role": "user", "content": f"执行报错：{result['error']}\n请修正代码重试。"},
            ]
            retry_response = ""
            async for chunk_text in self._stream_llm(retry_messages):
                retry_response += chunk_text
                yield {"event": "message_chunk", "data": {"text": chunk_text}}

            retry_code = self._extract_code(retry_response)
            if retry_code:
                yield {"event": "script", "data": {"code": retry_code}}
                yield {"event": "executing", "data": {}}
                result = await self._execute_command(sandbox_session_id, retry_code)
                if result["success"]:
                    code = retry_code
                    full_response = retry_response

        # 7. Save to history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": full_response})
        self._trim_history(session_id)

        # 8. Build step data if successful
        step_data = None
        if result["success"]:
            # Extract function body (strip def run(page): line)
            body = self._extract_function_body(code)
            step_data = {
                "action": "ai_script",
                "source": "ai",
                "value": body,
                "description": message,
                "prompt": message,
            }

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
            },
        }
        yield {"event": "done", "data": {}}

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        elements_json: str,
        history: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        # Build steps summary
        steps_text = ""
        if steps:
            lines = []
            for i, s in enumerate(steps, 1):
                source = s.get("source", "record")
                desc = s.get("description", s.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

        # Build elements summary
        elements_text = ""
        try:
            els = json.loads(elements_json) if elements_json else []
            lines = []
            for el in els:
                parts = [f"[{el['index']}]"]
                if el.get("role"):
                    parts.append(el["role"])
                parts.append(el["tag"])
                if el.get("name"):
                    parts.append(f'"{el["name"]}"')
                if el.get("placeholder"):
                    parts.append(f'placeholder="{el["placeholder"]}"')
                if el.get("href"):
                    parts.append(f'href="{el["href"]}"')
                if el.get("type"):
                    parts.append(f'type={el["type"]}')
                lines.append(" ".join(parts))
            elements_text = "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            elements_text = "(无法获取页面元素)"

        context = f"""## 历史操作步骤
{steps_text or "(暂无步骤)"}

## 当前页面可交互元素
{elements_text or "(无法获取)"}

## 用户指令
{user_message}"""

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": context})
        return messages

    async def _stream_llm(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        """Stream LLM response chunks."""
        model = get_llm_model(streaming=True)
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

        lc_messages = []
        for m in messages:
            if m["role"] == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            elif m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))

        async for chunk in model.astream(lc_messages):
            if chunk.content:
                yield chunk.content

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """Extract python code block from LLM response."""
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Fallback: look for def run(page):
        pattern2 = r"(def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        return None

    @staticmethod
    def _extract_function_body(code: str) -> str:
        """Extract the body of def run(page): for storage in step.value."""
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            if line.strip().startswith("def run("):
                in_body = True
                continue
            if in_body:
                # Dedent one level (4 spaces)
                if line.startswith("    "):
                    body_lines.append(line[4:])
                elif line.strip() == "":
                    body_lines.append("")
                else:
                    body_lines.append(line)
        return "\n".join(body_lines).strip()

    async def _get_page_elements(self, sandbox_session_id: str) -> str:
        """Extract interactive elements from the recording browser via command file."""
        # Write a command that evaluates JS and returns the result
        extract_code = f'''def run(page):
    result = page.evaluate("""{EXTRACT_ELEMENTS_JS}""")
    return result
'''
        result = await self._execute_command(sandbox_session_id, extract_code)
        if result["success"]:
            return result.get("output", "[]")
        logger.warning(f"Failed to extract elements: {result.get('error', '')[:200]}")
        return "[]"

    async def _execute_command(self, sandbox_session_id: str, code: str) -> Dict[str, Any]:
        """Write command file to sandbox and poll for result."""
        # Clean up any previous result
        await self._exec_cmd(sandbox_session_id, "rm -f /tmp/rpa_command_result.json")

        # Write command file via base64 to avoid escaping issues
        encoded = base64.b64encode(code.encode()).decode()
        write_code = (
            "import base64\n"
            f"data = base64.b64decode('{encoded}')\n"
            "with open('/tmp/rpa_command.py', 'wb') as f:\n"
            "    f.write(data)\n"
            "print('ok')"
        )
        await self._exec_code(sandbox_session_id, write_code)

        # Poll for result (every 500ms, up to 30s)
        for _ in range(60):
            await asyncio.sleep(0.5)
            raw = await self._exec_cmd(
                sandbox_session_id,
                "cat /tmp/rpa_command_result.json 2>/dev/null"
            )
            if raw.strip():
                try:
                    return json.loads(raw.strip())
                except json.JSONDecodeError:
                    continue

        return {"success": False, "output": "", "error": "Command execution timed out (30s)"}

    async def _exec_cmd(self, session_id: str, cmd: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_bash", "arguments": {"cmd": cmd}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("result", {}).get("structuredContent", {}).get("output", "")

    async def _exec_code(self, session_id: str, code: str) -> str:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "sandbox_execute_code", "arguments": {"code": code, "language": "python"}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.sandbox_url}/mcp",
                json=payload,
                headers={
                    "X-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            sc = result.get("result", {}).get("structuredContent", {})
            return sc.get("stdout") or sc.get("output") or ""
```

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/backend/rpa/assistant.py
git commit -m "feat(rpa): create RPAAssistant class with LLM streaming and command execution"
```

---

### Task 4: Add SSE chat endpoint to route/rpa.py

**Files:**
- Modify: `ScienceClaw/backend/route/rpa.py:1-17` (imports and module-level)
- Modify: `ScienceClaw/backend/route/rpa.py:136` (after save endpoint, before websocket)

- [ ] **Step 1: Add imports and assistant instance**

Add to the imports section at the top of `route/rpa.py`:

```python
import json
from sse_starlette.sse import EventSourceResponse
from backend.rpa.assistant import RPAAssistant
```

After the existing module-level instances (line 17: `exporter = SkillExporter()`), add:

```python
assistant = RPAAssistant(rpa_manager.sandbox_url)
```

- [ ] **Step 2: Add ChatRequest model**

After the `SaveSkillRequest` class (line 31), add:

```python
class ChatRequest(BaseModel):
    message: str
```

- [ ] **Step 3: Add the SSE chat endpoint**

After the `save_skill` endpoint (after line 136), add:

```python
@router.post("/session/{session_id}/chat")
async def chat_with_assistant(
    session_id: str,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    steps = [step.model_dump() for step in session.steps]

    async def event_generator():
        try:
            async for event in assistant.chat(
                session_id=session_id,
                sandbox_session_id=session.sandbox_session_id,
                message=request.message,
                steps=steps,
            ):
                evt_type = event.get("event", "message")
                evt_data = event.get("data", {})

                # If execution succeeded and returned a step, add it to session
                if evt_type == "result" and evt_data.get("success") and evt_data.get("step"):
                    step_data = evt_data["step"]
                    await rpa_manager.add_step(session_id, step_data)

                yield {
                    "event": evt_type,
                    "data": json.dumps(evt_data, ensure_ascii=False),
                }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}, ensure_ascii=False),
            }
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
```

- [ ] **Step 4: Commit**

```bash
git add ScienceClaw/backend/route/rpa.py
git commit -m "feat(rpa): add SSE chat endpoint for AI recording assistant"
```

---

### Task 5: Update generator.py to handle AI steps

**Files:**
- Modify: `ScienceClaw/backend/rpa/generator.py:16-131` (generate_script method)

- [ ] **Step 1: Add `_sync_to_async` method**

After the `_maybe_parameterize` method (end of file), add:

```python
    @staticmethod
    def _sync_to_async(code: str) -> str:
        """Convert Playwright sync API code to async by adding await."""
        import re as _re
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            # Add await to page.xxx() calls and variable.xxx() chains that use page methods
            if stripped and not stripped.startswith("#") and not stripped.startswith("def "):
                # Match lines that contain page.something( or end with a Playwright call
                if _re.search(r'\bpage\.', stripped):
                    # Don't double-await
                    if not stripped.startswith("await "):
                        # For assignments like `el = page.locator(...)`, await the right side
                        assign_match = _re.match(r'^(\w[\w\s,]*=\s*)(page\..+)$', stripped)
                        if assign_match:
                            result.append(f"{indent}{assign_match.group(1)}await {assign_match.group(2)}")
                            continue
                        # For standalone page.xxx() calls
                        result.append(f"{indent}await {stripped}")
                        continue
            result.append(line)
        return "\n".join(result)
```

- [ ] **Step 2: Handle `ai_script` steps in the main loop**

In the `generate_script` method, inside the `for step in deduped:` loop (around line 43), add handling for `ai_script` action. After the `desc` comment line (line 50-51) and before the navigation check (line 54), add:

```python
            # AI-generated script — embed directly with sync→async conversion
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    for code_line in converted.split("\n"):
                        lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.append("")
                continue
```

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/rpa/generator.py
git commit -m "feat(rpa): handle ai_script steps in generator with sync-to-async conversion"
```

---

### Task 6: Wire up RecorderPage.vue to SSE chat endpoint

**Files:**
- Modify: `ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue`

- [ ] **Step 1: Update the ChatMessage type and add reactive state**

Replace the existing refs at lines 27-28:

```typescript
const chatMessages = ref<any[]>([]);
const newMessage = ref('');
```

With:

```typescript
interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  script?: string
  status?: 'thinking' | 'executing' | 'success' | 'error'
  error?: string
  time: string
}

const chatMessages = ref<ChatMessage[]>([]);
const newMessage = ref('');
const isSending = ref(false);
```

- [ ] **Step 2: Replace the sendMessage function**

Replace the existing `sendMessage` function (lines 113-121) with:

```typescript
const sendMessage = async () => {
  const msg = newMessage.value.trim();
  if (!msg || isSending.value || !sessionId.value) return;

  isSending.value = true;
  chatMessages.value.push({
    role: 'user',
    text: msg,
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  });
  newMessage.value = '';

  const assistantMsg: ChatMessage = {
    role: 'assistant',
    text: '',
    status: 'thinking',
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  };
  chatMessages.value.push(assistantMsg);
  const idx = chatMessages.value.length - 1;

  try {
    const token = localStorage.getItem('token');
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const url = `${baseUrl}/api/v1/rpa/session/${sessionId.value}/chat`;

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ message: msg }),
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventType = 'message';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split('\n');
      buffer = parts.pop() || '';

      for (const line of parts) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          const dataStr = line.slice(5).trim();
          try {
            const data = JSON.parse(dataStr);
            const m = chatMessages.value[idx];
            if (eventType === 'message_chunk') {
              m.text += data.text || '';
            } else if (eventType === 'script') {
              m.script = data.code || '';
            } else if (eventType === 'executing') {
              m.status = 'executing';
            } else if (eventType === 'result') {
              m.status = data.success ? 'success' : 'error';
              if (!data.success) m.error = data.error || '执行失败';
              if (data.success && data.step) {
                // Add AI step to the local steps list
                steps.value.push({
                  id: Date.now().toString(),
                  title: data.step.description,
                  description: data.step.description,
                  status: 'done',
                  source: 'ai',
                });
              }
            } else if (eventType === 'error') {
              m.status = 'error';
              m.error = data.message || '未知错误';
            }
          } catch { /* skip non-JSON */ }
          eventType = 'message';
        }
      }
    }
  } catch (err: any) {
    chatMessages.value[idx].status = 'error';
    chatMessages.value[idx].error = err.message || '请求失败';
  } finally {
    isSending.value = false;
  }
};
```

- [ ] **Step 3: Update the chat message template to show status and code**

Replace the chat messages display section (lines 242-257) with:

```html
          <div
            v-for="(msg, idx) in chatMessages"
            :key="idx"
            class="flex flex-col gap-1.5"
            :class="msg.role === 'user' ? 'items-end' : 'items-start'"
          >
            <div
              class="max-w-[85%] p-3 rounded-2xl text-xs leading-relaxed"
              :class="msg.role === 'user'
                ? 'bg-[#831bd7] text-white rounded-tr-none shadow-md shadow-purple-100'
                : 'bg-[#eff1f2] text-gray-700 rounded-tl-none border border-gray-100'"
            >
              <!-- Status indicator for assistant messages -->
              <div v-if="msg.role === 'assistant' && msg.status === 'thinking'" class="flex items-center gap-2 mb-1">
                <div class="w-1.5 h-1.5 rounded-full bg-purple-500 animate-pulse"></div>
                <span class="text-[10px] text-purple-600 font-medium">思考中...</span>
              </div>
              <div v-if="msg.role === 'assistant' && msg.status === 'executing'" class="flex items-center gap-2 mb-1">
                <div class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></div>
                <span class="text-[10px] text-blue-600 font-medium">执行中...</span>
              </div>

              <!-- Message text -->
              <div class="whitespace-pre-wrap">{{ msg.text }}</div>

              <!-- Code block (collapsible) -->
              <details v-if="msg.script" class="mt-2">
                <summary class="text-[10px] text-purple-600 cursor-pointer font-medium">查看代码</summary>
                <pre class="mt-1 p-2 bg-gray-900 text-green-400 rounded-lg text-[10px] overflow-x-auto"><code>{{ msg.script }}</code></pre>
              </details>

              <!-- Result status -->
              <div v-if="msg.status === 'success'" class="mt-2 flex items-center gap-1 text-green-600">
                <CheckCircle :size="12" />
                <span class="text-[10px] font-medium">执行成功</span>
              </div>
              <div v-if="msg.status === 'error' && msg.error" class="mt-2 text-red-500 text-[10px]">
                执行失败: {{ msg.error }}
              </div>
            </div>
            <span class="text-[9px] text-gray-400 font-medium px-1">{{ msg.time }}</span>
          </div>
```

- [ ] **Step 4: Disable input while sending**

Update the input and send button (lines 260-276) to respect `isSending`:

```html
        <div class="p-4 bg-gray-50 border-t border-gray-100">
          <div class="relative">
            <input
              v-model="newMessage"
              @keyup.enter="sendMessage"
              :disabled="isSending"
              class="w-full bg-white border border-gray-200 rounded-2xl py-3 pl-4 pr-12 text-xs focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm placeholder:text-gray-400 outline-none disabled:opacity-50"
              :placeholder="isSending ? 'AI 正在处理...' : '向助手提问...'"
              type="text"
            />
            <button
              @click="sendMessage"
              :disabled="isSending"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-[#831bd7] hover:scale-110 transition-transform p-1.5 disabled:opacity-50 disabled:hover:scale-100"
            >
              <Send :size="16" />
            </button>
          </div>
        </div>
```

- [ ] **Step 5: Update step list to show AI steps differently**

In the steps list template (left sidebar), find where steps are rendered and add AI step styling. Look for the step rendering loop and add a purple indicator for AI steps:

In the step item rendering, add a conditional class for AI source:

```html
              <div
                class="w-2 h-2 rounded-full mt-1.5 flex-shrink-0"
                :class="step.source === 'ai' ? 'bg-purple-500' : 'bg-blue-500'"
              ></div>
```

And for AI steps, prefix the description with an AI indicator:

```html
              <span v-if="step.source === 'ai'" class="text-[9px] text-purple-600 font-bold mr-1">AI</span>
```

- [ ] **Step 6: Commit**

```bash
git add ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "feat(rpa): wire RecorderPage chat to SSE endpoint with streaming UI"
```

---

### Task 7: Integration test — manual verification

- [ ] **Step 1: Start the backend**

```bash
cd ScienceClaw/backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Verify no import errors on startup. Check that the `/rpa/session/{id}/chat` endpoint is registered in the OpenAPI docs at `http://localhost:8000/docs`.

- [ ] **Step 2: Start the frontend**

```bash
cd ScienceClaw/frontend
npm run dev
```

- [ ] **Step 3: Test the full flow**

1. Open the recorder page, start a recording session
2. In VNC, navigate to a website (e.g., google.com)
3. In the AI chat panel, type "点击搜索框" and send
4. Verify: streaming text appears in the chat bubble, code block shows, VNC shows the action executing
5. Verify: the step appears in the left sidebar with purple AI indicator
6. Stop recording, go to configure page, verify AI steps appear in the step list
7. Generate script, verify AI steps are embedded as async code in the final script

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(rpa): integration fixes for AI recording assistant"
```

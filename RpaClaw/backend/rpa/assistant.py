import json
import logging
import re
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional, Callable

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model

# Active ReAct agent instances keyed by session_id
_active_agents: Dict[str, "RPAReActAgent"] = {}

logger = logging.getLogger(__name__)

ELEMENT_EXTRACTION_TIMEOUT_S = 5.0
EXECUTION_TIMEOUT_S = 60.0
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_CONTENT_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

SYSTEM_PROMPT = """你是一个 RPA 录制助手。用户正在录制浏览器自动化技能，你需要根据用户的自然语言描述，结合当前页面状态和历史操作，生成 Playwright 异步 API 代码片段。

规则：
1. 生成的代码必须使用 Playwright 异步 API（await page.locator().click()）
2. 代码必须定义 async def run(page): 函数
3. 使用动态适应的选择器：
   - "点击第一个搜索结果" → await page.locator("h3").first.click() 或 await page.locator("[data-result]").first.click()
   - "获取表格数据" → await page.locator("table").first.inner_text()
   - 不要硬编码具体文本内容，用位置/结构/角色选择
4. 操作之间加 await page.wait_for_timeout(500) 等待 UI 响应
5. 打开外部网站时，优先使用 await page.goto(url, wait_until="domcontentloaded")，不要默认等待 load
6. 用 ```python 代码块包裹代码
7. 代码之外可以附带简短说明"""

# JS to extract interactive elements from the page
EXTRACT_ELEMENTS_JS = r"""() => {
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

        const key = tag + role + name + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name.replace(/\s+/g, ' ');
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value && tag !== 'input') info.value = value.substring(0, 80);
        if (type) info.type = type;
        const checked = el.checked;
        if (checked !== undefined) info.checked = checked;

        results.push(info);
        index++;
        if (index > 150) break;
    }
    return JSON.stringify(results);
}"""


class RPAAssistant:
    """AI recording assistant: takes natural language, generates and executes Playwright code."""

    def __init__(self):
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream AI assistant response. Yields SSE event dicts.

        Args:
            session_id: RPA session ID
            page: The Playwright page object for this session
            message: User's natural language instruction
            steps: Current recorded steps
            model_config: Optional LLM model config override
        """
        # 1. Get page elements directly via page.evaluate
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面...\n\n"}}
        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return
        elements_json = await self._get_page_elements(current_page)

        # 2. Build prompt
        history = self._get_history(session_id)
        messages = self._build_messages(message, steps, elements_json, history)

        # 3. Stream LLM response
        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        # 4. Extract code
        code = self._extract_code(full_response)
        if not code:
            yield {"event": "error", "data": {"message": "未能从 AI 响应中提取到代码"}}
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": full_response})
            self._trim_history(session_id)
            yield {"event": "done", "data": {}}
            return

        yield {"event": "script", "data": {"code": code}}

        # 5. Execute directly on the page object
        yield {"event": "executing", "data": {}}
        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return
        result = await self._execute_on_page(current_page, code)

        if not result["success"]:
            # 6. Retry once with error context
            yield {"event": "message_chunk", "data": {"text": "\n\n执行失败，正在重试...\n\n"}}
            retry_messages = messages + [
                {"role": "assistant", "content": full_response},
                {"role": "user", "content": f"执行报错：{result['error']}\n请修正代码重试。"},
            ]
            retry_response = ""
            async for chunk_text in self._stream_llm(retry_messages, model_config):
                retry_response += chunk_text
                yield {"event": "message_chunk", "data": {"text": chunk_text}}

            retry_code = self._extract_code(retry_response)
            if retry_code:
                yield {"event": "script", "data": {"code": retry_code}}
                yield {"event": "executing", "data": {}}
                current_page = page_provider() if page_provider else page
                if current_page is None:
                    yield {"event": "error", "data": {"message": "No active page available"}}
                    yield {"event": "done", "data": {}}
                    return
                result = await self._execute_on_page(current_page, retry_code)
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
        steps_text = ""
        if steps:
            lines = []
            for i, s in enumerate(steps, 1):
                source = s.get("source", "record")
                desc = s.get("description", s.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

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

    async def _stream_llm(self, messages: List[Dict[str, str]], model_config: Optional[Dict[str, Any]] = None) -> AsyncGenerator[str, None]:
        """Stream LLM response chunks."""
        model = get_llm_model(config=model_config, streaming=True)
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
        # Try async def run pattern
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        # Fallback: sync pattern
        pattern3 = r"(def run\(page\):.*)"
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            return match3.group(1).strip()
        return None

    @staticmethod
    def _extract_function_body(code: str) -> str:
        """Extract the body of async def run(page): for storage in step.value."""
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("async def run(") or stripped.startswith("def run("):
                in_body = True
                continue
            if in_body:
                if line.startswith("    "):
                    body_lines.append(line[4:])
                elif line.strip() == "":
                    body_lines.append("")
                else:
                    body_lines.append(line)
        return "\n".join(body_lines).strip()

    async def _get_page_elements(self, page: Page) -> str:
        return await _get_page_elements(page)

    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        return await _execute_on_page(page, code)


async def _get_page_elements(page: Page) -> str:
    """Extract interactive elements directly from the page."""
    try:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        result = await asyncio.wait_for(
            page.evaluate(EXTRACT_ELEMENTS_JS),
            timeout=ELEMENT_EXTRACTION_TIMEOUT_S,
        )
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as e:
        logger.warning(f"Failed to extract elements from {page.url!r}: {e}")
        return "[]"


async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object."""
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined"}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc()}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass


def _extract_llm_response_text(response: Any) -> str:
    """Normalize LangChain AIMessage content into a plain text response."""
    content = getattr(response, "content", "")
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}

    reasoning = additional_kwargs.get("reasoning_content", "")
    fallback_text = reasoning.strip() if isinstance(reasoning, str) else ""

    if isinstance(content, list):
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    thinking_parts.append(str(block.get("thinking", "")).strip())
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        clean = "\n".join(part.strip() for part in text_parts if str(part).strip()).strip()
        if clean:
            return clean
        thoughts = "\n".join(part for part in thinking_parts if part).strip()
        return thoughts or fallback_text

    if isinstance(content, str):
        clean = THINK_TAG_RE.sub("", content).strip()
        if clean:
            return clean
        if not fallback_text:
            matches = THINK_CONTENT_RE.findall(content)
            fallback_text = "\n".join(match.strip() for match in matches if match.strip()).strip()
        return fallback_text

    if content is None:
        return fallback_text

    text = str(content).strip()
    return text or fallback_text


def _extract_llm_chunk_text(chunk: Any) -> str:
    """Extract displayable text from a streamed chunk."""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        return "".join(text_parts)
    if isinstance(content, str):
        return THINK_TAG_RE.sub("", content)
    return ""


def _extract_llm_chunk_fallback_text(chunk: Any) -> str:
    """Extract reasoning/thinking fallback text from a streamed chunk."""
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        thoughts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thought = str(block.get("thinking", "")).strip()
                if thought:
                    thoughts.append(thought)
        return "\n".join(thoughts).strip()

    if isinstance(content, str):
        matches = THINK_CONTENT_RE.findall(content)
        return "\n".join(match.strip() for match in matches if match.strip()).strip()

    return ""


REACT_SYSTEM_PROMPT = """你是一个 RPA 自动化 Agent。用户给你一个目标任务，你需要通过观察当前页面状态，逐步规划并执行浏览器操作，直到任务完成。

每一轮你必须输出一个 JSON 对象（不要包裹在代码块中），格式如下：
{
  "thought": "当前状态分析和下一步计划",
  "action": "execute|done|abort",
  "code": "单个原子操作的 Playwright 代码（仅 action=execute 时需要）",
  "description": "这一步操作的简短描述（动词+宾语，如：点击第一个搜索结果）",
  "risk": "none|high",
  "risk_reason": "高危原因（仅 risk=high 时需要）"
}

规则：
1. action=execute：执行一个原子操作（1-3行代码）
2. action=done：任务已完成，不再需要操作
3. action=abort：任务无法完成，在 thought 中说明原因
4. risk=high：表单提交、删除、支付、授权等不可逆操作必须标记
5. code 必须是完整可执行的 Python 代码片段（不需要 def run 包装），使用 await page.xxx()
   - 正确：await page.click("button")
   - 正确：title = await page.locator("h1").inner_text()  ← 赋值语句，await 在右边
6. 数据提取任务：如果用户要求"获取"、"读取"、"提取"数据，必须在最后一步执行提取操作并返回
   - 示例：title = await page.locator("h1").first.inner_text()
   - 然后在 description 中说明提取到的数据（如"获取到标题：xxx"）
   - 不要只是"看到了"就 done，要实际执行提取代码

【选择器规则 - 严格遵守】
- 固定目标（如"打开 GitHub"、"点击 Issues"）：可以用文本/href/属性选择器
  - 正确：await page.click("text=Issues")
  - 正确：await page.goto("https://github.com/trending")
  - 正确：await page.click("a[href*='/issues']")

- 动态位置（如"第N个"、"列表数据"、"排名第一"）：必须用位置/结构选择器
  - 正确：await page.locator("article").first.click()  ← 第一个
  - 正确：await page.locator("ul li a").nth(2).click()  ← 第三个
  - 正确：await page.locator("table tr").all()  ← 列表数据
  - 错误：await page.click("a[href='/luongnv89/claude-howto']")  ← 硬编码动态内容
  - 错误：await page.click("text=某个具体标题")  ← 硬编码动态文本

- 数据提取任务：必须显式提取并返回数据
  - 如果任务是"获取标题"，必须执行 inner_text()/text_content() 并在 code 中包含提取逻辑
  - 不要只是"看到了"就 done，要实际提取数据

6. 每步操作后会告知你执行结果，据此调整下一步
7. 如果连续失败，尝试更简单的选择器，不要退化为硬编码值"""


class RPAReActAgent:
    """ReAct-based autonomous agent: Observe → Think → Act loop."""

    MAX_STEPS = 20

    def __init__(self):
        self._confirm_event: Optional[asyncio.Event] = None
        self._confirm_approved: bool = False
        self._aborted: bool = False
        self._history: List[Dict[str, str]] = []  # persists across turns

    def resolve_confirm(self, approved: bool) -> None:
        self._confirm_approved = approved
        if self._confirm_event:
            self._confirm_event.set()

    def abort(self) -> None:
        self._aborted = True
        if self._confirm_event:
            self._confirm_event.set()

    async def run(
        self,
        session_id: str,
        page: Page,
        goal: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._aborted = False
        steps_done = 0

        # Append new user goal to persistent history
        steps_summary = ""
        if existing_steps:
            lines = [f"{i+1}. {s.get('description', s.get('action', ''))}" for i, s in enumerate(existing_steps)]
            steps_summary = "\n已有步骤：\n" + "\n".join(lines) + "\n"
        self._history.append({"role": "user", "content": f"目标任务：{goal}{steps_summary}"})

        for iteration in range(self.MAX_STEPS):
            if self._aborted:
                yield {"event": "agent_aborted", "data": {"reason": "用户中止"}}
                return

            # Observe
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            elements_json = await _get_page_elements(current_page)
            url = current_page.url
            try:
                title = await current_page.title()
            except Exception:
                title = ""

            obs = self._build_observation(url, title, elements_json, steps_done)
            self._history.append({"role": "user", "content": obs})

            # Think — stream LLM response
            full_response = ""
            async for chunk in self._stream_llm(self._history, model_config):
                full_response += chunk

            self._history.append({"role": "assistant", "content": full_response})

            # Parse JSON
            parsed = self._parse_json(full_response)
            if not parsed:
                yield {"event": "agent_aborted", "data": {"reason": f"无法解析 Agent 响应: {full_response[:200]}"}}
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action", "execute")
            code = parsed.get("code", "")
            description = parsed.get("description", "操作")
            risk = parsed.get("risk", "none")
            risk_reason = parsed.get("risk_reason", "")

            yield {"event": "agent_thought", "data": {"text": thought}}

            if action == "done":
                yield {"event": "agent_done", "data": {"total_steps": steps_done}}
                return

            if action == "abort":
                yield {"event": "agent_aborted", "data": {"reason": thought}}
                return

            # Wrap bare code in async def run(page) if needed
            executable = self._wrap_code(code)

            # High-risk confirmation
            if risk == "high":
                self._confirm_event = asyncio.Event()
                self._confirm_approved = False
                yield {"event": "confirm_required", "data": {
                    "description": description,
                    "risk_reason": risk_reason,
                    "code": code,
                }}
                await self._confirm_event.wait()
                self._confirm_event = None
                if self._aborted:
                    yield {"event": "agent_aborted", "data": {"reason": "用户中止"}}
                    return
                if not self._confirm_approved:
                    self._history.append({"role": "user", "content": "用户跳过了该步骤，请继续下一步或完成任务。"})
                    continue

            # Act
            yield {"event": "agent_action", "data": {"description": description, "code": code}}
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            result = await _execute_on_page(current_page, executable)
            if result["success"]:
                steps_done += 1
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": code,
                    "description": description,
                    "prompt": goal,
                }
                output = result.get("output", "")
                # If there's meaningful output, append to description for visibility
                if output and output != "ok" and output != "None":
                    yield {"event": "agent_step_done", "data": {"step": step_data, "output": output}}
                    self._history.append({"role": "user", "content": f"执行成功：{description}\n输出：{output}"})
                else:
                    yield {"event": "agent_step_done", "data": {"step": step_data}}
                    self._history.append({"role": "user", "content": f"执行成功：{description}"})
            else:
                error_msg = result.get("error", "未知错误")
                self._history.append({"role": "user", "content": f"执行失败：{error_msg[:500]}\n请分析错误并调整策略。"})

        yield {"event": "agent_done", "data": {"total_steps": steps_done}}

    @staticmethod
    def _build_observation(url: str, title: str, elements_json: str, steps_done: int) -> str:
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
                lines.append(" ".join(parts))
            elements_text = "\n".join(lines) or "(无可交互元素)"
        except Exception:
            elements_text = "(无法获取)"

        return f"""当前页面状态：
URL: {url}
标题: {title}
已完成步骤数: {steps_done}

可交互元素:
{elements_text}

请输出下一步操作的 JSON。"""

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        # Try raw JSON first
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        # Try extracting from code block
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # Try finding { ... } block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    @staticmethod
    def _wrap_code(code: str) -> str:
        """Wrap bare code in async def run(page) if not already wrapped."""
        stripped = code.strip()
        if stripped.startswith("async def run(") or stripped.startswith("def run("):
            return stripped
        indented = "\n".join("    " + line for line in stripped.splitlines())
        return f"async def run(page):\n{indented}"

    @staticmethod
    async def _stream_llm(
        history: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        model = get_llm_model(config=model_config, streaming=True)
        lc_messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
        for m in history:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
        if hasattr(model, "astream"):
            text_parts: List[str] = []
            fallback_parts: List[str] = []
            async for chunk in model.astream(lc_messages):
                text = _extract_llm_chunk_text(chunk)
                if text:
                    text_parts.append(text)
                    continue
                fallback = _extract_llm_chunk_fallback_text(chunk)
                if fallback:
                    fallback_parts.append(fallback)
            full_text = "".join(text_parts)
            if full_text.strip():
                yield full_text
                return
            fallback_text = "\n".join(part for part in fallback_parts if part).strip()
            if fallback_text:
                yield fallback_text
                return

        response = await model.ainvoke(lc_messages)
        yield _extract_llm_response_text(response)

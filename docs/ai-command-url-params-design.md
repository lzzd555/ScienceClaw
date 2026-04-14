# AI Command URL 参数化方案

## 问题

`skill.py` 中的 `_AI_COMMAND_URL` 在生成/导出时被硬编码为固定地址（如 `http://127.0.0.1:8000/api/v1/rpa/ai-command`）。

在沙箱模式下，sandbox 容器访问 backend 的地址不是 `127.0.0.1:8000`（可能是 `http://host.docker.internal:12001` 或其他 Docker 网络地址），导致 skill 调用 ai_command 失败。

## 目标

将 `_ai_command_url` 作为参数管理，使其在整个生命周期中都能获取到正确的地址：

1. **录制测试时** — 自动使用环境变量或从请求中计算的地址
2. **保存导出时** — 自动将地址写入 `params.json`
3. **后续执行时** — 从 `params.json` 读取，executor 场景通过 kwargs 注入覆盖

## 参数获取优先级

```
executor kwargs._ai_command_url   （executor 注入，最高优先级）
    ↓ fallback
params.json._ai_command_url       （导出时写入）
    ↓ fallback
模板硬编码 _AI_COMMAND_URL         （生成时从环境变量计算）
```

## 当前代码链路分析

### 环境变量

`RPA_AI_COMMAND_URL` 环境变量可在 `.env` 中配置，作为系统默认的 ai_command URL。

### 地址计算逻辑（已存在）

`route/rpa.py` 中的 `_build_ai_command_url_for_request()` 负责：
1. 优先读 `RPA_AI_COMMAND_URL` 环境变量
2. 如果没有，从 HTTP 请求的 `base_url` 推算
3. 沙箱模式下自动将 `127.0.0.1` / `localhost` 替换为 `host.docker.internal`

### 测试阶段（已正常工作）

`test_script` 路由调用了 `_build_ai_command_url_for_request()` 计算出正确 URL，传给 `generator.generate_script()` 写入脚本。

### 保存阶段（缺失）

`save_skill` 路由**没有**调用 `_build_ai_command_url_for_request()`，也没有把 URL 传给 `skill_exporter.export_skill()`，导致 params.json 中缺失该参数。

### 执行阶段（缺失）

skill.py 独立运行时不读取 params.json，executor 执行时也不注入 `_ai_command_url`。

## 改动方案

### 1. `route/rpa.py` — save_skill 路由补传 ai_command_url

当前 save 和 test 走了不同逻辑，test 有 `_build_ai_command_url_for_request`，save 没有。需要补上：

```python
@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
    http_request: Request = None,  # 新增
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    is_local_mode = settings.storage_backend == "local"
    ai_command_url = _build_ai_command_url_for_request(http_request, is_local=is_local_mode)  # 新增
    script = generator.generate_script(
        steps, request.params, is_local=is_local_mode,
        ai_command_url=ai_command_url,  # 新增
    )

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
        ai_command_url=ai_command_url,  # 新增
    )

    session.status = "saved"
    return {"status": "success", "skill_name": skill_name}
```

### 2. `skill_exporter.py` — 导出时自动注入到 params.json

`export_skill` 新增 `ai_command_url` 参数，写入 params.json：

```python
async def export_skill(
    self,
    user_id: str,
    skill_name: str,
    description: str,
    script: str,
    params: Dict[str, Any],
    ai_command_url: str = "",  # 新增
) -> str:
    ...
    # 写入 params.json 时注入 _ai_command_url
    export_params = dict(params)
    if ai_command_url:
        export_params["_ai_command_url"] = ai_command_url

    if settings.storage_backend == "local":
        ...
        (skill_dir / "params.json").write_text(
            json.dumps(export_params, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        # MongoDB 模式：params 字段包含 _ai_command_url
        await col.update_one(
            ...,
            {"$set": {
                ...
                "params": export_params,  # 使用包含 _ai_command_url 的 params
                ...
            }},
        )
```

### 3. `generator.py` 模板 — skill.py 运行时读取 params.json

修改两个 `RUNNER_TEMPLATE`（Docker 和 Local），让 skill.py 启动时从 params.json 读取 `_ai_command_url`：

#### 3a. `_ai_command` 函数改为接收 url 参数

```python
async def _ai_command(prompt: str, mode: str, page, token: str, url: str = None):
    """Call AI with prompt. mode='execute' runs Playwright code, mode='data' returns text."""
    _target_url = url or _AI_COMMAND_URL
    _ctx = ""
    try:
        _ctx = await page.inner_text("body")
        if len(_ctx) > 50000:
            _ctx = _ctx[:50000]
    except Exception:
        pass
    _headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=120) as _c:
        _r = await _c.post(
            _target_url,
            json={"prompt": prompt, "page_context": _ctx, "mode": mode},
            headers=_headers
        )
        ...
```

#### 3b. `execute_skill` 从 kwargs 取 `_ai_command_url` 并传递

```python
async def execute_skill(page, **kwargs):
    """Auto-generated skill from RPA recording."""
    _results = {}
    _ai_cmd_url = kwargs.get("_ai_command_url", _AI_COMMAND_URL)
    ...
    # 所有 _ai_command 调用处加上 url=_ai_cmd_url
    await _ai_command("...", "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)
```

对应 `generate_script` 中生成 `_ai_command` 调用的代码也要同步修改（约在 279、299 行）：

```python
# 原来
step_lines.append(f'    await _ai_command("{operation_prompt}", "execute", current_page, kwargs.get("_ai_token", ""))')

# 改为
step_lines.append(f'    await _ai_command("{operation_prompt}", "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')
```

#### 3c. `main()` 函数从 params.json 加载参数

```python
async def main():
    kwargs = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    # 从 params.json 加载配置（命令行参数优先）
    try:
        from pathlib import Path as _P
        _pj = _P(__file__).parent / "params.json"
        if _pj.exists():
            _loaded = _json.loads(_pj.read_text(encoding="utf-8"))
            _loaded.update(kwargs)
            kwargs = _loaded
    except Exception:
        pass

    # ... 启动浏览器、执行 skill
```

## 改动文件清单

| 文件 | 改动点 |
|------|--------|
| `RpaClaw/backend/route/rpa.py` | `save_skill` 加 `http_request` 参数，计算 `ai_command_url` 并传给 generator 和 exporter |
| `RpaClaw/backend/rpa/skill_exporter.py` | `export_skill` 新增 `ai_command_url` 参数，写入 params.json |
| `RpaClaw/backend/rpa/generator.py` | 两个模板中 `_ai_command` 加 `url` 参数；`execute_skill` 从 kwargs 取 `_ai_command_url`；`main()` 加 params.json 加载逻辑；`generate_script` 中生成调用代码的行同步修改 |

## 影响范围

- **向后兼容**：已导出的旧 skill.py 不受影响，其硬编码的 `_AI_COMMAND_URL` 仍然生效
- **新 skill 可靠**：导出时 params.json 包含正确地址，skill.py 启动时自动加载
- **沙箱模式安全**：executor 通过 kwargs 注入当前环境正确的 URL，覆盖 params.json 中的值
- **命令行覆盖**：`python skill.py --_ai_command_url=http://xxx` 可手动覆盖

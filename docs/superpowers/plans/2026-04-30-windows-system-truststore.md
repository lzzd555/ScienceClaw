# Windows System Trust Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows 本地后端默认使用系统证书库进行出站 HTTPS 校验，修复 API Monitor MCP 工具测试在 Python/httpx 中不信任 Windows 已安装 CA 的问题。

**Architecture:** 新增一个轻量 `backend.tls_trust` 启动模块，集中决定是否调用 `truststore.inject_into_ssl()`。该模块只读取环境变量和平台信息，延迟导入 `truststore`，并在 `backend/main.py` 早期执行，确保后续 `httpx.AsyncClient` 共享同一 TLS 信任策略。

**Tech Stack:** Python 3.13、FastAPI、httpx、truststore、pytest、loguru、python-dotenv。

---

## 依据的设计规格

本计划实现中文 spec：

```text
docs/superpowers/specs/2026-04-30-windows-system-truststore-design.md
```

当前已确认根因：

```text
Postman 使用 Windows 系统证书存储
Python/httpx 默认使用 Python/OpenSSL/certifi 证书解析
API Monitor MCP 工具测试由 backend 进程中的 httpx.AsyncClient 执行
```

因此本实现是 backend 进程级 TLS 信任策略，不是只针对某一个 tool test 的局部绕过。

---

## 文件结构

- 新建 `RpaClaw/backend/tls_trust.py`
  - 负责读取 `RPA_USE_SYSTEM_TRUSTSTORE`
  - 判断平台是否为 Windows
  - 判断是否存在显式 CA bundle 环境变量
  - 延迟导入并调用 `truststore.inject_into_ssl()`
  - 输出可诊断日志

- 新建 `RpaClaw/backend/tests/test_tls_trust.py`
  - 覆盖 spec 决策表
  - 使用 fake importer 避免测试依赖真实系统证书库
  - 验证注入失败不会抛出异常

- 修改 `RpaClaw/backend/main.py`
  - 在业务路由导入前调用 `configure_tls_trust()`
  - 保证后续创建 `httpx.AsyncClient` 前已完成 TLS 策略注入

- 修改 `RpaClaw/backend/requirements.txt`
  - 新增 `truststore>=0.10.0`

- 检查 `RpaClaw/backend/.env.template`
  - 确认已有 `RPA_USE_SYSTEM_TRUSTSTORE=auto`
  - 确认 `auto / true / false` 中文说明存在

---

### Task 1: 为 TLS 信任策略写失败单测

**Files:**
- Create: `RpaClaw/backend/tests/test_tls_trust.py`

- [ ] **Step 1: 新建失败单测文件**

Create `RpaClaw/backend/tests/test_tls_trust.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.tls_trust import configure_tls_trust


@dataclass
class FakeTruststore:
    calls: int = 0
    should_fail: bool = False

    def inject_into_ssl(self) -> None:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("boom")


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, message: str, *args: Any) -> None:
        self.messages.append(("debug", message.format(*args)))

    def info(self, message: str, *args: Any) -> None:
        self.messages.append(("info", message.format(*args)))

    def warning(self, message: str, *args: Any) -> None:
        self.messages.append(("warning", message.format(*args)))

    def joined(self) -> str:
        return "\n".join(message for _, message in self.messages)


def _importer(fake_truststore: FakeTruststore):
    def import_module(name: str):
        assert name == "truststore"
        return fake_truststore

    return import_module


def test_false_mode_never_imports_truststore() -> None:
    imported: list[str] = []
    logger = FakeLogger()

    def import_module(name: str):
        imported.append(name)
        raise AssertionError("truststore should not be imported")

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "false"},
        platform="win32",
        import_module=import_module,
        logger=logger,
    )

    assert result == "python-default"
    assert imported == []
    assert "Python default" in logger.joined()


def test_auto_windows_without_explicit_ca_injects_truststore() -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "system-truststore"
    assert fake_truststore.calls == 1
    assert "system via truststore" in logger.joined()


@pytest.mark.parametrize("env_key", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_auto_windows_with_explicit_ca_skips_truststore(env_key: str) -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto", env_key: "C:/certs/ca-bundle.crt"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "explicit-ca"
    assert fake_truststore.calls == 0
    assert "explicit CA bundle environment detected" in logger.joined()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_auto_non_windows_skips_truststore(platform: str) -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "auto"},
        platform=platform,
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "python-default"
    assert fake_truststore.calls == 0


def test_true_mode_attempts_injection_even_with_explicit_ca() -> None:
    fake_truststore = FakeTruststore()
    logger = FakeLogger()

    result = configure_tls_trust(
        env={
            "RPA_USE_SYSTEM_TRUSTSTORE": "true",
            "SSL_CERT_FILE": "C:/certs/ca-bundle.crt",
        },
        platform="linux",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "system-truststore"
    assert fake_truststore.calls == 1
    assert "explicit CA bundle environment detected" in logger.joined()


def test_injection_failure_logs_warning_and_falls_back() -> None:
    fake_truststore = FakeTruststore(should_fail=True)
    logger = FakeLogger()

    result = configure_tls_trust(
        env={"RPA_USE_SYSTEM_TRUSTSTORE": "true"},
        platform="win32",
        import_module=_importer(fake_truststore),
        logger=logger,
    )

    assert result == "python-default"
    assert fake_truststore.calls == 1
    assert "truststore injection failed" in logger.joined()
```

- [ ] **Step 2: 运行测试，确认失败来自模块缺失**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_tls_trust.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'backend.tls_trust'
```

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/tests/test_tls_trust.py
git commit -m "test: add TLS truststore strategy coverage"
```

---

### Task 2: 实现 TLS truststore 启动模块

**Files:**
- Create: `RpaClaw/backend/tls_trust.py`
- Test: `RpaClaw/backend/tests/test_tls_trust.py`

- [ ] **Step 1: 新建 `backend/tls_trust.py`**

Create `RpaClaw/backend/tls_trust.py` with this content:

```python
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Protocol

from loguru import logger as default_logger


CA_BUNDLE_ENV_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
TRUSTSTORE_ENV_KEY = "RPA_USE_SYSTEM_TRUSTSTORE"
DEFAULT_TRUSTSTORE_MODE = "auto"


class _Logger(Protocol):
    def debug(self, message: str, *args: object) -> None: ...
    def info(self, message: str, *args: object) -> None: ...
    def warning(self, message: str, *args: object) -> None: ...


def _normalize_mode(value: str | None) -> str:
    mode = (value or DEFAULT_TRUSTSTORE_MODE).strip().lower()
    if mode in {"1", "yes", "y", "on"}:
        return "true"
    if mode in {"0", "no", "n", "off"}:
        return "false"
    if mode in {"auto", "true", "false"}:
        return mode
    return DEFAULT_TRUSTSTORE_MODE


def _has_explicit_ca_bundle(env: Mapping[str, str]) -> bool:
    return any((env.get(key) or "").strip() for key in CA_BUNDLE_ENV_KEYS)


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def configure_tls_trust(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    import_module: Callable[[str], ModuleType] = importlib.import_module,
    logger: _Logger = default_logger,
) -> str:
    """Configure process-wide TLS trust behavior for outbound Python HTTPS clients."""

    current_env = env if env is not None else os.environ
    current_platform = platform if platform is not None else sys.platform
    mode = _normalize_mode(current_env.get(TRUSTSTORE_ENV_KEY))
    has_explicit_ca = _has_explicit_ca_bundle(current_env)

    if mode == "false":
        logger.debug("TLS trust store: Python default")
        return "python-default"

    if mode == "auto" and has_explicit_ca:
        logger.info("TLS trust store: explicit CA bundle environment detected; system truststore not injected")
        return "explicit-ca"

    if mode == "auto" and not _is_windows(current_platform):
        logger.debug("TLS trust store: Python default")
        return "python-default"

    if mode == "true" and has_explicit_ca:
        logger.info("TLS trust store: explicit CA bundle environment detected; attempting system truststore injection")

    try:
        truststore = import_module("truststore")
        truststore.inject_into_ssl()
    except Exception as exc:
        logger.warning("TLS trust store: truststore injection failed; falling back to Python default ({})", exc)
        return "python-default"

    logger.info("TLS trust store: system via truststore")
    return "system-truststore"
```

- [ ] **Step 2: 运行 TLS 单测，确认通过**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_tls_trust.py -q
```

Expected:

```text
9 passed
```

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/tls_trust.py RpaClaw/backend/tests/test_tls_trust.py
git commit -m "feat: add backend TLS truststore bootstrap"
```

---

### Task 3: 接入后端启动流程与依赖

**Files:**
- Modify: `RpaClaw/backend/main.py`
- Modify: `RpaClaw/backend/requirements.txt`
- Test: `RpaClaw/backend/tests/test_tls_trust.py`

- [ ] **Step 1: 在 requirements 中加入 truststore**

Modify `RpaClaw/backend/requirements.txt` and insert this line near the other HTTP/runtime dependencies:

```text
truststore>=0.10.0
```

Recommended location:

```text
httpx==0.28.1
truststore>=0.10.0
tavily-python==0.7.21
```

- [ ] **Step 2: 在 `main.py` 早期调用 TLS bootstrap**

Modify the top of `RpaClaw/backend/main.py`.

Change from:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager

from backend.storage import init_storage, close_storage, get_repository
```

To:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager

from backend.tls_trust import configure_tls_trust


configure_tls_trust()

from backend.storage import init_storage, close_storage, get_repository
```

The blank lines are intentional: they make the early process-level bootstrap visible before the rest of the backend imports.

- [ ] **Step 3: 运行 TLS 单测**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_tls_trust.py -q
```

Expected:

```text
9 passed
```

- [ ] **Step 4: 运行 main 相关轻量回归测试**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_main_frontend_dist.py tests/test_mcp_route.py::test_api_monitor_tool_test_valid_delegates_to_runtime -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/main.py RpaClaw/backend/requirements.txt
git commit -m "feat: enable system truststore during backend startup"
```

---

### Task 4: 环境模板与最终验证

**Files:**
- Verify: `RpaClaw/backend/.env.template`
- Verify: `docs/superpowers/specs/2026-04-30-windows-system-truststore-design.md`
- Test: `RpaClaw/backend/tests/test_tls_trust.py`
- Test: `RpaClaw/backend/tests/test_main_frontend_dist.py`
- Test: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: 确认 `.env.template` 包含 TLS 配置**

Run:

```bash
rg -n "RPA_USE_SYSTEM_TRUSTSTORE|TLS / 证书信任|Windows 本地模式默认使用系统证书库" RpaClaw/backend/.env.template
```

Expected output includes:

```text
RPA_USE_SYSTEM_TRUSTSTORE=auto
```

If the entry is missing, add this block after `LOCAL_PATH_STYLE=windows`:

```env
# ── TLS / 证书信任 ──
# auto  -> Windows 本地模式默认使用系统证书库；显式 CA bundle 环境变量存在时跳过
# true  -> 任意平台都尝试使用系统证书库
# false -> 使用 Python 默认 CA bundle
RPA_USE_SYSTEM_TRUSTSTORE=auto
```

- [ ] **Step 2: 运行目标测试集合**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_tls_trust.py tests/test_main_frontend_dist.py tests/test_mcp_route.py::test_api_monitor_tool_test_valid_delegates_to_runtime -q
```

Expected:

```text
12 passed
```

- [ ] **Step 3: 手工验证 Windows HTTPS 行为**

On Windows, run from `RpaClaw/backend` with no explicit CA bundle variables:

```powershell
Remove-Item Env:SSL_CERT_FILE -ErrorAction SilentlyContinue
Remove-Item Env:REQUESTS_CA_BUNDLE -ErrorAction SilentlyContinue
Remove-Item Env:CURL_CA_BUNDLE -ErrorAction SilentlyContinue
$env:RPA_USE_SYSTEM_TRUSTSTORE="auto"
uv run python -c "from backend.tls_trust import configure_tls_trust; print(configure_tls_trust())"
```

Expected:

```text
system-truststore
```

Then start the backend:

```powershell
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Expected backend log includes:

```text
TLS trust store: system via truststore
```

- [ ] **Step 4: 手工验证显式 CA bundle 优先级**

On Windows, run:

```powershell
$env:RPA_USE_SYSTEM_TRUSTSTORE="auto"
$env:SSL_CERT_FILE="C:\certs\ca-bundle.crt"
uv run python -c "from backend.tls_trust import configure_tls_trust; print(configure_tls_trust())"
```

Expected:

```text
explicit-ca
```

- [ ] **Step 5: 检查工作树只包含本计划范围内文件**

Run:

```bash
git status --short
```

Expected changed files are limited to:

```text
RpaClaw/backend/tls_trust.py
RpaClaw/backend/tests/test_tls_trust.py
RpaClaw/backend/main.py
RpaClaw/backend/requirements.txt
RpaClaw/backend/.env.template
```

`RpaClaw/backend/.env.template` may already be committed before implementation; if so it should not appear in this final status.

- [ ] **Step 6: Commit final docs/template adjustment if needed**

If `.env.template` changed in this task:

```bash
git add RpaClaw/backend/.env.template
git commit -m "docs: document system truststore environment option"
```

If `.env.template` had already been updated and no files changed in this task, do not create an empty commit.

---

## Final Verification

Run the focused verification suite:

```bash
cd RpaClaw/backend
uv run pytest tests/test_tls_trust.py tests/test_main_frontend_dist.py tests/test_mcp_route.py::test_api_monitor_tool_test_valid_delegates_to_runtime -q
```

Expected:

```text
12 passed
```

Run a dependency import smoke check:

```bash
cd RpaClaw/backend
uv run python -c "import truststore; from backend.tls_trust import configure_tls_trust; print('ok')"
```

Expected:

```text
ok
```

On Windows, confirm API Monitor MCP tool test succeeds against an HTTPS endpoint whose CA is installed in Windows Trusted Root Certification Authorities.

---

## 实施备注

- 不要添加 `verify=False`。
- 不要设置 `NODE_TLS_REJECT_UNAUTHORIZED=0`。
- 不要修改 API Monitor MCP 的请求执行模型。
- 不要把用户的证书内容写入日志。
- `truststore` 必须延迟导入，避免 `RPA_USE_SYSTEM_TRUSTSTORE=false` 时也强依赖该包的导入副作用。
- 若 `truststore` 注入失败，后端继续启动，并保留原始 HTTPS 错误给调用方排查。

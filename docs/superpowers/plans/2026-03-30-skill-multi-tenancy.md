# Skill Multi-Tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate external skill storage from shared filesystem to MongoDB with per-user isolation, supporting multi-instance backend deployment.

**Architecture:** External skills stored as MongoDB documents with `user_id` field for tenant isolation. Builtin skills remain on filesystem (Docker image). Agent execution layer uses a new `MongoSkillBackend` that implements the deepagents Backend protocol against MongoDB. Sandbox skill injection uses session-scoped paths for isolation.

**Tech Stack:** MongoDB (Motor async driver), FastAPI, deepagents Backend protocol, Pydantic v2

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/mongodb/db.py` | Modify | Add `skills` collection indexes, remove `blocked_skills` index |
| `backend/deepagent/mongo_skill_backend.py` | Create | MongoDB-backed Backend implementation for skills |
| `backend/deepagent/agent.py` | Modify | Use `MongoSkillBackend`, remove `get_blocked_skills()`, update `_build_backend()` |
| `backend/route/sessions.py` | Modify | All skill CRUD endpoints read/write MongoDB instead of filesystem |
| `backend/rpa/skill_exporter.py` | Modify | Async, write to MongoDB instead of filesystem |
| `backend/route/rpa.py` | Modify | Pass `user_id` to exporter |
| `scripts/migrate_skills_to_mongo.py` | Create | One-time migration script |
| `docker-compose.yml` | Modify | Remove `./Skills:/app/Skills` volume mount |

---

### Task 1: Add `skills` collection indexes to MongoDB

**Files:**
- Modify: `ScienceClaw/backend/mongodb/db.py:44-85`

- [ ] **Step 1: Add skills collection indexes**

In `init_indexes()`, add the new `skills` indexes and remove the `blocked_skills` index. Insert after the session_events indexes (line 63) and replace the blocked_skills index (lines 66-68):

```python
        # Skills collection (multi-tenant)
        await cls.db.skills.create_index(
            [("user_id", 1), ("name", 1)], unique=True
        )
        await cls.db.skills.create_index(
            [("user_id", 1), ("blocked", 1)]
        )
```

Remove these lines (66-68):
```python
        # Blocked skills collection   ← DELETE
        await cls.db.blocked_skills.create_index(   ← DELETE
            [("user_id", 1), ("skill_name", 1)], unique=True   ← DELETE
        )   ← DELETE
```

- [ ] **Step 2: Verify backend starts without errors**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.mongodb.db import db; print('OK')"`
Expected: `OK` (import succeeds)

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/mongodb/db.py
git commit -m "feat(db): add skills collection indexes, remove blocked_skills index"
```

- [ ] **Step 4: Add `get_blocked_skill_names` helper for agent layer**

Add a helper function at the end of `db.py` (after the `db = MongoDB` line) that queries blocked skills from the new `skills` collection:

```python
async def get_blocked_skill_names(user_id: str) -> set[str]:
    """Query blocked skill names for a user from the skills collection."""
    col = MongoDB.get_collection("skills")
    cursor = col.find(
        {"user_id": user_id, "blocked": True},
        {"name": 1}
    )
    names = set()
    async for doc in cursor:
        if doc.get("name"):
            names.add(doc["name"])
    return names
```

---

### Task 2: Create MongoSkillBackend

**Files:**
- Create: `ScienceClaw/backend/deepagent/mongo_skill_backend.py`

This backend implements the deepagents Backend protocol, serving skill files from MongoDB instead of the filesystem. It must implement both sync and async versions of: `ls_info`, `read`, `write`, `glob_info`, `grep_raw`.

- [ ] **Step 1: Create the MongoSkillBackend file**

```python
"""
MongoSkillBackend — MongoDB-backed skill storage for deepagents.

Replaces FilteredFilesystemBackend. All skill files are stored in MongoDB
`skills` collection. Blocked skills are filtered via the `blocked` field
on the document itself (no separate blocked_skills collection).
"""
from __future__ import annotations

import re
import fnmatch
from datetime import datetime, timezone
from typing import Set, List, Optional, Dict, Any

from loguru import logger
from deepagents.backends.protocol import (
    EditResult,
    FileInfo,
    GrepMatch,
    WriteResult,
)


def _now():
    return datetime.now(timezone.utc)


class MongoSkillBackend:
    """Backend that serves skill files from MongoDB.

    Path convention: /<skill_name>/SKILL.md, /<skill_name>/skill.py, etc.
    Root listing (/) returns all non-blocked skills as directories.
    """

    def __init__(self, user_id: str, blocked_skills: Set[str] | None = None):
        self._user_id = user_id
        self._blocked = set(blocked_skills or [])

    def _get_col(self):
        from backend.mongodb.db import db
        return db.get_collection("skills")

    def _base_filter(self) -> dict:
        return {"user_id": self._user_id}

    def _active_filter(self) -> dict:
        f = self._base_filter()
        if self._blocked:
            f["name"] = {"$nin": list(self._blocked)}
        return f

    def _skill_name_from_path(self, path: str) -> str:
        return path.strip("/").split("/")[0] if path.strip("/") else ""

    def _file_name_from_path(self, path: str) -> str:
        parts = path.strip("/").split("/", 1)
        return parts[1] if len(parts) > 1 else ""

    # ── ls ────────────────────────────────────────────────────────

    def ls_info(self, path: str) -> list[FileInfo]:
        raise NotImplementedError("Use async als_info")

    async def als_info(self, path: str) -> list[FileInfo]:
        col = self._get_col()
        skill_name = self._skill_name_from_path(path)

        if not skill_name:
            # Root listing: return all skills as directories
            cursor = col.find(
                self._active_filter(),
                {"name": 1, "description": 1}
            )
            entries = []
            async for doc in cursor:
                entries.append({
                    "path": f"/{doc['name']}",
                    "name": doc["name"],
                    "type": "directory",
                    "size": 0,
                })
            return entries

        # Listing a specific skill: return its files
        if skill_name in self._blocked:
            return []
        doc = await col.find_one(
            {**self._base_filter(), "name": skill_name},
            {"files": 1}
        )
        if not doc or not doc.get("files"):
            return []
        entries = []
        for fname, content in doc["files"].items():
            entries.append({
                "path": f"/{skill_name}/{fname}",
                "name": fname,
                "type": "file",
                "size": len(content.encode("utf-8")) if content else 0,
            })
        return entries

    # ── read ──────────────────────────────────────────────────────

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        raise NotImplementedError("Use async aread")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        skill_name = self._skill_name_from_path(file_path)
        file_name = self._file_name_from_path(file_path)

        if not skill_name or not file_name:
            raise FileNotFoundError(f"Invalid path: {file_path}")
        if skill_name in self._blocked:
            raise FileNotFoundError(f"Skill is blocked: {skill_name}")

        col = self._get_col()
        doc = await col.find_one(
            {**self._base_filter(), "name": skill_name},
            {f"files.{file_name}": 1}
        )
        if not doc or not doc.get("files", {}).get(file_name):
            raise FileNotFoundError(f"File not found: {file_path}")

        content = doc["files"][file_name]
        lines = content.split("\n")
        selected = lines[offset:offset + limit]
        return "\n".join(selected)

    # ── write ─────────────────────────────────────────────────────

    def write(self, file_path: str, content: str) -> WriteResult:
        raise NotImplementedError("Use async awrite")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        skill_name = self._skill_name_from_path(file_path)
        file_name = self._file_name_from_path(file_path)

        if not skill_name:
            raise PermissionError(f"Invalid path: {file_path}")
        if skill_name in self._blocked:
            raise PermissionError(f"Skill is blocked: {skill_name}")

        col = self._get_col()

        if not file_name:
            # Creating a new skill directory (no-op, skill created on first file write)
            return {"path": file_path, "status": "ok"}

        # Upsert: create skill doc if not exists, set file content
        result = await col.update_one(
            {**self._base_filter(), "name": skill_name},
            {
                "$set": {
                    f"files.{file_name}": content,
                    "updated_at": _now(),
                },
                "$setOnInsert": {
                    "user_id": self._user_id,
                    "name": skill_name,
                    "description": "",
                    "source": "agent",
                    "blocked": False,
                    "params": {},
                    "created_at": _now(),
                },
            },
            upsert=True,
        )

        # If SKILL.md was written, parse frontmatter to update description
        if file_name == "SKILL.md":
            await self._update_description_from_frontmatter(skill_name, content)

        return {"path": file_path, "status": "ok"}

    async def _update_description_from_frontmatter(self, skill_name: str, content: str):
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return
        try:
            import yaml
            fm = yaml.safe_load(match.group(1))
            if isinstance(fm, dict) and fm.get("description"):
                col = self._get_col()
                await col.update_one(
                    {**self._base_filter(), "name": skill_name},
                    {"$set": {"description": fm["description"]}}
                )
        except Exception:
            pass

    # ── edit ──────────────────────────────────────────────────────

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> EditResult:
        raise NotImplementedError("Use async aedit")

    async def aedit(self, file_path: str, old_string: str, new_string: str,
                    replace_all: bool = False) -> EditResult:
        skill_name = self._skill_name_from_path(file_path)
        file_name = self._file_name_from_path(file_path)

        if not skill_name or not file_name:
            raise PermissionError(f"Invalid path: {file_path}")
        if skill_name in self._blocked:
            raise PermissionError(f"Skill is blocked: {skill_name}")

        # Read current content
        content = await self.aread(file_path, offset=0, limit=100000)

        if old_string not in content:
            return {"path": file_path, "status": "error",
                    "message": f"old_string not found in {file_path}"}

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        await self.awrite(file_path, new_content)
        return {"path": file_path, "status": "ok"}

    # ── glob ──────────────────────────────────────────────────────

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        raise NotImplementedError("Use async aglob_info")

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        # Get all files, then filter by glob pattern
        col = self._get_col()
        cursor = col.find(self._active_filter(), {"name": 1, "files": 1})
        results = []
        async for doc in cursor:
            skill_name = doc["name"]
            for fname in doc.get("files", {}):
                full_path = f"/{skill_name}/{fname}"
                if fnmatch.fnmatch(full_path, pattern) or fnmatch.fnmatch(fname, pattern):
                    results.append({
                        "path": full_path,
                        "name": fname,
                        "type": "file",
                        "size": len(doc["files"][fname].encode("utf-8")),
                    })
        return results

    # ── grep ──────────────────────────────────────────────────────

    def grep_raw(self, pattern: str, path: str | None = None,
                 glob: str | None = None) -> list[GrepMatch] | str:
        raise NotImplementedError("Use async agrep_raw")

    async def agrep_raw(self, pattern: str, path: str | None = None,
                        glob: str | None = None) -> list[GrepMatch] | str:
        col = self._get_col()
        filt = self._active_filter()

        if path:
            skill_name = self._skill_name_from_path(path)
            if skill_name:
                filt["name"] = skill_name

        cursor = col.find(filt, {"name": 1, "files": 1})
        results = []
        try:
            regex = re.compile(pattern)
        except re.error:
            return f"Invalid regex: {pattern}"

        async for doc in cursor:
            skill_name = doc["name"]
            for fname, content in doc.get("files", {}).items():
                full_path = f"/{skill_name}/{fname}"
                if glob and not fnmatch.fnmatch(fname, glob):
                    continue
                for line_num, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        results.append({
                            "file": full_path,
                            "line": line_num,
                            "text": line,
                        })
        return results
```

- [ ] **Step 2: Verify import works**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.deepagent.mongo_skill_backend import MongoSkillBackend; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ScienceClaw/backend/deepagent/mongo_skill_backend.py
git commit -m "feat: add MongoSkillBackend — MongoDB-backed skill storage for deepagents"
```

---

### Task 3: Migrate `sessions.py` skill CRUD endpoints to MongoDB

**Files:**
- Modify: `ScienceClaw/backend/route/sessions.py:714-960`

All skill endpoints currently scan the filesystem and use the `blocked_skills` collection. Rewrite them to use the `skills` collection with `user_id` isolation.

- [ ] **Step 1: Replace `_list_skill_dirs` and `list_skills` endpoint**

Remove the `_parse_skill_frontmatter` function (lines 722-738) and `_list_skill_dirs` function (lines 741-755). Replace the `list_skills` endpoint (lines 762-785) with:

```python
@router.get("/skills", response_model=ApiResponse)
async def list_skills(current_user: User = Depends(require_user)) -> ApiResponse:
    """列出所有 skills（内置 + 用户外置）。"""
    try:
        # Builtin skills from filesystem (shared, read-only)
        builtin = _list_skill_dirs(_BUILTIN_SKILLS_DIR, builtin=True)

        # User's external skills from MongoDB
        col = _db.get_collection("skills")
        cursor = col.find(
            {"user_id": current_user.id},
            {"name": 1, "description": 1, "files": 1, "blocked": 1}
        )
        external = []
        async for doc in cursor:
            files = list(doc.get("files", {}).keys())
            external.append({
                "name": doc.get("name", ""),
                "description": doc.get("description", ""),
                "files": files,
                "builtin": False,
                "blocked": doc.get("blocked", False),
            })

        for s in builtin:
            s["blocked"] = False

        return ApiResponse(data=builtin + external)
    except Exception as exc:
        logger.exception("list_skills failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Keep `_parse_skill_frontmatter` and `_list_skill_dirs` — they're still needed for builtin skills listing.

- [ ] **Step 2: Replace `toggle_block_skill` endpoint**

Replace lines 788-805 with:

```python
@router.put("/skills/{skill_name}/block", response_model=ApiResponse)
async def toggle_block_skill(
    skill_name: str,
    body: SkillBlockRequest,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    """屏蔽或取消屏蔽一个外置 skill。"""
    try:
        col = _db.get_collection("skills")
        result = await col.update_one(
            {"user_id": current_user.id, "name": skill_name},
            {"$set": {"blocked": body.blocked}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        return ApiResponse(data={"skill_name": skill_name, "blocked": body.blocked})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("toggle_block_skill failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 3: Replace `delete_skill` endpoint**

Replace lines 808-830 with:

```python
@router.delete("/skills/{skill_name}", response_model=ApiResponse)
async def delete_skill(
    skill_name: str,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    """彻底删除一个外置 skill。"""
    try:
        col = _db.get_collection("skills")
        result = await col.delete_one(
            {"user_id": current_user.id, "name": skill_name}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        return ApiResponse(data={"skill_name": skill_name, "deleted": True})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete_skill failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Replace `save_skill_from_session` endpoint**

Replace lines 837-893. The new version reads skill files from the sandbox workspace and writes them to MongoDB:

```python
@router.post("/{session_id}/skills/save", response_model=ApiResponse)
async def save_skill_from_session(
    session_id: str,
    body: SaveSkillRequest,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    """Save a skill from session workspace to MongoDB."""
    try:
        session = await async_get_science_session(session_id)
        if session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        skill_name = body.skill_name.strip()
        if not skill_name or "/" in skill_name or "\\" in skill_name:
            raise HTTPException(status_code=400, detail="Invalid skill name")

        # Check candidate paths in sandbox workspace
        candidate_paths = [
            _Path(_WORKSPACE_DIR) / session_id / ".agents" / "skills" / skill_name,
            _Path(_WORKSPACE_DIR) / session_id / "skills" / skill_name,
            _Path(_WORKSPACE_DIR) / session_id / skill_name,
        ]
        src = next(
            (p for p in candidate_paths if p.is_dir() and (p / "SKILL.md").is_file()),
            None,
        )

        if src is None:
            # Check if skill already exists in MongoDB (in-place update case)
            col = _db.get_collection("skills")
            existing = await col.find_one(
                {"user_id": current_user.id, "name": skill_name},
                {"_id": 1}
            )
            if existing:
                return ApiResponse(data={"skill_name": skill_name, "saved": True})
            raise HTTPException(
                status_code=404,
                detail=f"Skill '{skill_name}' not found in session workspace",
            )

        # Read all files from the skill directory
        files = {}
        for fp in src.rglob("*"):
            if fp.is_file():
                rel = str(fp.relative_to(src))
                try:
                    files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        # Parse description from SKILL.md frontmatter
        description = ""
        skill_md = files.get("SKILL.md", "")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md, re.DOTALL)
        if fm_match:
            try:
                fm = _yaml.safe_load(fm_match.group(1))
                if isinstance(fm, dict):
                    description = fm.get("description", "")
            except Exception:
                pass

        # Upsert to MongoDB
        col = _db.get_collection("skills")
        now = datetime.now(timezone.utc)
        await col.update_one(
            {"user_id": current_user.id, "name": skill_name},
            {
                "$set": {
                    "files": files,
                    "description": description,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": current_user.id,
                    "name": skill_name,
                    "source": "agent",
                    "blocked": False,
                    "params": {},
                    "created_at": now,
                },
            },
            upsert=True,
        )

        return ApiResponse(data={"skill_name": skill_name, "saved": True})
    except ScienceSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("save_skill_from_session failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 5: Replace `list_skill_files` and `read_skill_file` endpoints**

Replace `list_skill_files` (lines 896-928) with:

```python
@router.get("/skills/{skill_name}/files", response_model=ApiResponse)
async def list_skill_files(
    skill_name: str,
    path: str = "",
    current_user: User = Depends(require_user),
) -> ApiResponse:
    """列出某个外置 skill 内部的文件结构。"""
    try:
        col = _db.get_collection("skills")
        doc = await col.find_one(
            {"user_id": current_user.id, "name": skill_name},
            {"files": 1}
        )
        if not doc:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        items = []
        for fname in sorted(doc.get("files", {}).keys()):
            items.append({
                "name": fname,
                "path": fname,
                "type": "file",
            })
        return ApiResponse(data=items)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("list_skill_files failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

Replace `read_skill_file` (lines 935-957) with:

```python
@router.post("/skills/{skill_name}/read", response_model=ApiResponse)
async def read_skill_file(
    skill_name: str,
    body: ReadSkillFileRequest,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    """读取某个外置 skill 内的文件内容。"""
    try:
        col = _db.get_collection("skills")
        doc = await col.find_one(
            {"user_id": current_user.id, "name": skill_name},
            {f"files.{body.file}": 1}
        )
        if not doc:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        content = doc.get("files", {}).get(body.file)
        if content is None:
            raise HTTPException(status_code=404, detail="File not found")
        return ApiResponse(data={"file": body.file, "content": content})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("read_skill_file failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 6: Remove unused imports**

Remove `import shutil` if no longer used elsewhere in the file. Remove the `blocked_skills` collection references.

- [ ] **Step 7: Verify backend starts**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.route.sessions import router; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add ScienceClaw/backend/route/sessions.py
git commit -m "feat(sessions): migrate skill CRUD endpoints from filesystem to MongoDB"
```

---

### Task 4: Modify `agent.py` — Use MongoSkillBackend + remove deprecated code

**Files:**
- Modify: `ScienceClaw/backend/deepagent/agent.py:24-112, 277-410, 550-575`

Replace `FilteredFilesystemBackend` with `MongoSkillBackend` for the `/skills/` route. Remove `get_blocked_skills()`. The `/builtin-skills/` route stays on `FilesystemBackend`.

- [ ] **Step 1: Update imports**

In `agent.py`, replace the import of `FilteredFilesystemBackend`:

```python
# Old:
from backend.deepagent.filtered_backend import FilteredFilesystemBackend

# New:
from backend.deepagent.mongo_skill_backend import MongoSkillBackend
```

- [ ] **Step 2: Remove `_EXTERNAL_SKILLS_DIR` path constant**

Remove line 72:
```python
_EXTERNAL_SKILLS_DIR = os.environ.get("EXTERNAL_SKILLS_DIR", "/app/Skills")  # DELETE
```

Keep `_EXTERNAL_SKILLS_ROUTE = "/skills/"` — it's still used as the route prefix.

- [ ] **Step 3: Rewrite `_build_backend` function**

Replace the `_build_backend` function (lines 82-111) with:

```python
def _build_backend(
    session_id: str,
    sandbox: FullSandboxBackend,
    user_id: str | None = None,
    blocked_skills: Set[str] | None = None,
):
    """
    构建 CompositeBackend 工厂函数（会话级隔离）：
      - 默认: 传入的 FullSandboxBackend 实例
      - /builtin-skills/ 路由: FilesystemBackend（内置 skills，始终加载）
      - /skills/          路由: MongoSkillBackend（MongoDB 多租户 skills）
    """
    routes = {}

    if os.path.isdir(_BUILTIN_SKILLS_DIR):
        logger.info(f"[Skills] 内置 skills: {_BUILTIN_SKILLS_DIR} → {_BUILTIN_SKILLS_ROUTE}")
        routes[_BUILTIN_SKILLS_ROUTE] = FilesystemBackend(
            root_dir=_BUILTIN_SKILLS_DIR,
            virtual_mode=True,
        )

    if user_id:
        logger.info(f"[Skills] MongoDB skills for user={user_id} → {_EXTERNAL_SKILLS_ROUTE}"
                     f" (blocked: {blocked_skills or set()})")
        routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
            user_id=user_id,
            blocked_skills=blocked_skills,
        )

    if routes:
        return lambda rt: CompositeBackend(default=sandbox, routes=routes)
    else:
        return sandbox
```

- [ ] **Step 4: Replace `get_blocked_skills` function**

Replace the `get_blocked_skills` function (lines 277-291) with a call to the new helper:

```python
async def get_blocked_skills(user_id: str) -> Set[str]:
    """从 MongoDB skills 集合查询用户屏蔽的 skills 列表。"""
    try:
        from backend.mongodb.db import get_blocked_skill_names
        return await get_blocked_skill_names(user_id)
    except Exception as exc:
        logger.warning(f"[Skills] 查询屏蔽列表失败: {exc}")
        return set()
```

- [ ] **Step 5: Update `_build_backend` call in `deep_agent()`**

In the `deep_agent()` function, update the call to `_build_backend` (around line 372) to pass `user_id`:

```python
# Old:
backend = _build_backend(session_id, sandbox, blocked_skills=blocked_skills)

# New:
backend = _build_backend(session_id, sandbox, user_id=user_id, blocked_skills=blocked_skills)
```

- [ ] **Step 6: Remove `_EXTERNAL_SKILLS_DIR` references in `deep_agent()`**

Remove the `_dir_watcher.has_changed(_EXTERNAL_SKILLS_DIR)` call (line 345).

Update the skills_sources block (lines 403-407) — remove the filesystem check for external skills:

```python
# Old:
skills_sources: List[str] = []
if os.path.isdir(_BUILTIN_SKILLS_DIR):
    skills_sources.append(_BUILTIN_SKILLS_ROUTE)
if os.path.isdir(_EXTERNAL_SKILLS_DIR):
    skills_sources.append(_EXTERNAL_SKILLS_ROUTE)

# New:
skills_sources: List[str] = []
if os.path.isdir(_BUILTIN_SKILLS_DIR):
    skills_sources.append(_BUILTIN_SKILLS_ROUTE)
if user_id:
    skills_sources.append(_EXTERNAL_SKILLS_ROUTE)
```

- [ ] **Step 7: Update `deep_agent_eval()` function**

In `deep_agent_eval()` (lines 502-575), replace the `FilteredFilesystemBackend` usage with `MongoSkillBackend`. Update the skill_sources handling:

```python
    if skill_sources:
        for src in skill_sources:
            if src == _BUILTIN_SKILLS_ROUTE and os.path.isdir(_BUILTIN_SKILLS_DIR):
                routes[_BUILTIN_SKILLS_ROUTE] = FilesystemBackend(
                    root_dir=_BUILTIN_SKILLS_DIR, virtual_mode=True,
                )
                resolved_sources.append(_BUILTIN_SKILLS_ROUTE)
            elif src == _EXTERNAL_SKILLS_ROUTE:
                routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
                    user_id="eval_runner",
                    blocked_skills=set(),
                )
                resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)
    else:
        routes[_EXTERNAL_SKILLS_ROUTE] = MongoSkillBackend(
            user_id="eval_runner",
            blocked_skills=set(),
        )
        resolved_sources.append(_EXTERNAL_SKILLS_ROUTE)
```

- [ ] **Step 8: Remove `_EXTERNAL_SKILLS_DIR` from `_dir_watcher` import if unused**

Check if `_dir_watcher` is still used elsewhere. If only used for `_EXTERNAL_SKILLS_DIR`, remove the import.

- [ ] **Step 9: Verify import works**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.deepagent.agent import deep_agent; print('OK')"`
Expected: `OK`

- [ ] **Step 10: Commit**

```bash
git add ScienceClaw/backend/deepagent/agent.py
git commit -m "feat(agent): use MongoSkillBackend, remove FilteredFilesystemBackend dependency"
```

---

### Task 5: Migrate `skill_exporter.py` and `route/rpa.py` to MongoDB

**Files:**
- Modify: `ScienceClaw/backend/rpa/skill_exporter.py`
- Modify: `ScienceClaw/backend/route/rpa.py:149-170`

- [ ] **Step 1: Rewrite `skill_exporter.py` as async MongoDB writer**

Replace the entire file content:

```python
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from backend.mongodb.db import db

logger = logging.getLogger(__name__)


class SkillExporter:
    """Export recorded RPA skills to MongoDB."""

    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
    ) -> str:
        """Export skill to MongoDB skills collection.

        Returns the skill name on success.
        """
        # Generate input schema
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        for param_name, param_info in params.items():
            input_schema["properties"][param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            if param_info.get("required", False):
                input_schema["required"].append(param_name)

        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

See `skill.py` for the Playwright implementation.
"""

        now = datetime.now(timezone.utc)
        col = db.get_collection("skills")
        await col.update_one(
            {"user_id": user_id, "name": skill_name},
            {
                "$set": {
                    "files": {
                        "SKILL.md": skill_md,
                        "skill.py": script,
                    },
                    "description": description,
                    "params": params,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "name": skill_name,
                    "source": "rpa",
                    "blocked": False,
                    "created_at": now,
                },
            },
            upsert=True,
        )

        logger.info(f"Skill '{skill_name}' exported to MongoDB for user {user_id}")
        return skill_name
```

- [ ] **Step 2: Update `route/rpa.py` save endpoint**

In `route/rpa.py`, update the `save_skill` endpoint (lines 149-170) to pass `user_id` and `await` the async exporter:

```python
@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params)

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
    )

    session.status = "saved"
    return {"status": "success", "skill_name": skill_name}
```

- [ ] **Step 3: Verify import works**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.rpa.skill_exporter import SkillExporter; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add ScienceClaw/backend/rpa/skill_exporter.py ScienceClaw/backend/route/rpa.py
git commit -m "feat(rpa): migrate skill export from filesystem to MongoDB"
```

---

### Task 6: Create migration script

**Files:**
- Create: `ScienceClaw/scripts/migrate_skills_to_mongo.py`

One-time script to migrate existing filesystem skills to MongoDB.

- [ ] **Step 1: Create the migration script**

```python
"""
One-time migration: import existing filesystem skills into MongoDB.

Usage:
    cd ScienceClaw/backend
    uv run python -m scripts.migrate_skills_to_mongo --skills-dir /app/Skills --default-user-id <user_id>

If --default-user-id is not provided, uses the first user found in MongoDB.
"""
import asyncio
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    parser = argparse.ArgumentParser(description="Migrate filesystem skills to MongoDB")
    parser.add_argument("--skills-dir", default="/app/Skills", help="Path to Skills directory")
    parser.add_argument("--default-user-id", default=None, help="User ID to assign skills to")
    parser.add_argument("--mongodb-uri", default=None, help="MongoDB URI (default: from env)")
    parser.add_argument("--db-name", default="scienceclaw", help="Database name")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done")
    args = parser.parse_args()

    # Connect to MongoDB
    uri = args.mongodb_uri or os.environ.get(
        "MONGODB_URI",
        f"mongodb://{os.environ.get('MONGODB_USER', 'scienceone')}:"
        f"{os.environ.get('MONGODB_PASSWORD', '')}@"
        f"{os.environ.get('MONGODB_HOST', 'localhost')}:"
        f"{os.environ.get('MONGODB_PORT', '27014')}/",
    )
    client = AsyncIOMotorClient(uri)
    db = client[args.db_name]

    # Resolve user_id
    user_id = args.default_user_id
    if not user_id:
        user = await db.users.find_one({}, {"_id": 1})
        if not user:
            print("ERROR: No users found in MongoDB. Use --default-user-id.")
            sys.exit(1)
        user_id = str(user["_id"])
        print(f"Using first user: {user_id}")

    skills_dir = Path(args.skills_dir)
    if not skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {skills_dir}")
        sys.exit(1)

    # Migrate blocked_skills → set blocked=True on skill docs
    blocked_names = set()
    async for doc in db.blocked_skills.find({"user_id": user_id}, {"skill_name": 1}):
        if doc.get("skill_name"):
            blocked_names.add(doc["skill_name"])
    if blocked_names:
        print(f"Found {len(blocked_names)} blocked skills: {blocked_names}")

    # Scan and migrate skills
    migrated = 0
    skipped = 0
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "SKILL.md").is_file():
            continue

        skill_name = child.name

        # Check if already exists
        existing = await db.skills.find_one(
            {"user_id": user_id, "name": skill_name}, {"_id": 1}
        )
        if existing:
            print(f"  SKIP (exists): {skill_name}")
            skipped += 1
            continue

        # Read all files
        files = {}
        for fp in child.rglob("*"):
            if fp.is_file():
                rel = str(fp.relative_to(child))
                try:
                    files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    print(f"  WARN: Cannot read {fp}: {e}")

        # Parse description from SKILL.md
        description = ""
        skill_md = files.get("SKILL.md", "")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md, re.DOTALL)
        if fm_match:
            try:
                fm = yaml.safe_load(fm_match.group(1))
                if isinstance(fm, dict):
                    description = fm.get("description", "")
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "name": skill_name,
            "description": description,
            "source": "migrated",
            "blocked": skill_name in blocked_names,
            "files": files,
            "params": {},
            "created_at": now,
            "updated_at": now,
        }

        if args.dry_run:
            print(f"  DRY-RUN: {skill_name} ({len(files)} files, blocked={doc['blocked']})")
        else:
            await db.skills.insert_one(doc)
            print(f"  MIGRATED: {skill_name} ({len(files)} files, blocked={doc['blocked']})")
        migrated += 1

    print(f"\nDone. Migrated: {migrated}, Skipped: {skipped}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add ScienceClaw/scripts/migrate_skills_to_mongo.py
git commit -m "feat: add one-time skill migration script (filesystem → MongoDB)"
```

---

### Task 7: Cleanup — remove deprecated code and volume mounts

**Files:**
- Modify: `docker-compose.yml` (lines with `./Skills:/app/Skills`)
- Modify: `docker-compose-release.yml` (if applicable)
- Delete or deprecate: `ScienceClaw/backend/deepagent/filtered_backend.py`
- Modify: `ScienceClaw/backend/route/sessions.py` (remove `_EXTERNAL_SKILLS_DIR` references around line 1609)

- [ ] **Step 1: Remove `./Skills:/app/Skills` volume mount from `docker-compose.yml`**

In `docker-compose.yml`, remove the line:
```yaml
      - ./Skills:/app/Skills
```

from the backend service volumes. Keep `./ScienceClaw/backend/builtin_skills:/app/builtin_skills` — builtin skills stay on filesystem.

Also remove the websearch service's skills mount if present:
```yaml
      - ./Skills:/skills:ro
```

- [ ] **Step 2: Remove `./Skills:/app/Skills` from `docker-compose-release.yml`**

Same change in the release compose file.

- [ ] **Step 3: Delete `filtered_backend.py`**

```bash
git rm ScienceClaw/backend/deepagent/filtered_backend.py
```

- [ ] **Step 4: Clean up remaining `_EXTERNAL_SKILLS_DIR` references in `sessions.py`**

Around line 1609, there's a reference to `_EXTERNAL_SKILLS_DIR` for listing skill names. Replace it with a MongoDB query:

```python
# Old:
{
    d.name for d in _Path(_EXTERNAL_SKILLS_DIR).iterdir()
    if d.is_dir() and not d.name.startswith(".")
} if _Path(_EXTERNAL_SKILLS_DIR).is_dir() else set()

# New:
{
    doc["name"] async for doc in _db.get_collection("skills").find(
        {"user_id": current_user.id}, {"name": 1}
    )
}
```

Also remove the `_EXTERNAL_SKILLS_DIR` variable declaration (line 717) from `sessions.py`.

- [ ] **Step 5: Verify backend starts**

Run: `cd ScienceClaw/backend && uv run python -c "from backend.route.sessions import router; from backend.deepagent.agent import deep_agent; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git rm ScienceClaw/backend/deepagent/filtered_backend.py
git add docker-compose.yml docker-compose-release.yml ScienceClaw/backend/route/sessions.py
git commit -m "chore: remove filesystem skill storage, blocked_skills collection, and Skills volume mounts"
```

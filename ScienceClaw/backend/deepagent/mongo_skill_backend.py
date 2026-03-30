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
        # Fetch entire files dict — can't use dot-notation projection
        # because filenames like "SKILL.md" contain dots
        doc = await col.find_one(
            {**self._base_filter(), "name": skill_name},
            {"files": 1}
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

        # Read-modify-write: can't use dot-notation $set because filenames
        # like "SKILL.md" contain dots which MongoDB interprets as nested paths
        existing = await col.find_one(
            {**self._base_filter(), "name": skill_name},
            {"files": 1}
        )

        if existing:
            files = existing.get("files", {})
            files[file_name] = content
            await col.update_one(
                {**self._base_filter(), "name": skill_name},
                {"$set": {"files": files, "updated_at": _now()}},
            )
        else:
            await col.insert_one({
                "user_id": self._user_id,
                "name": skill_name,
                "description": "",
                "source": "agent",
                "blocked": False,
                "params": {},
                "files": {file_name: content},
                "created_at": _now(),
                "updated_at": _now(),
            })

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

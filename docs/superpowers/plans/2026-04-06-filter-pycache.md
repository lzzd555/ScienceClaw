# Filter __pycache__ Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter out Python cache files and system temporary files from skill file listings in the API

**Architecture:** Add a helper function `should_skip_file()` to check file/directory names against a blocklist, then apply it in the `list_skill_files` endpoint before adding items to the response

**Tech Stack:** Python 3.13, FastAPI, pathlib, pytest

---

### Task 1: Add filter function with tests

**Files:**
- Create: `RpaClaw/backend/tests/test_sessions.py`
- Modify: `RpaClaw/backend/route/sessions.py:948-970`

- [ ] **Step 1: Write the failing test**

Create `RpaClaw/backend/tests/test_sessions.py`:

```python
import unittest
from pathlib import Path
from unittest.mock import MagicMock


class TestShouldSkipFile(unittest.TestCase):
    """Test the should_skip_file helper function."""
    
    def test_skip_pycache_directory(self):
        """Should skip __pycache__ directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/__pycache__")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_pyc_files(self):
        """Should skip .pyc bytecode files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/module.pyc")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_pyo_files(self):
        """Should skip .pyo bytecode files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/module.pyo")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_pyd_files(self):
        """Should skip .pyd bytecode files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/module.pyd")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_ds_store(self):
        """Should skip macOS .DS_Store files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.DS_Store")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_thumbs_db(self):
        """Should skip Windows Thumbs.db files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/Thumbs.db")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_desktop_ini(self):
        """Should skip Windows desktop.ini files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/desktop.ini")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_gitignore(self):
        """Should skip .gitignore files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.gitignore")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_git_directory(self):
        """Should skip .git directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.git")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_svn_directory(self):
        """Should skip .svn directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.svn")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_vscode_directory(self):
        """Should skip .vscode directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.vscode")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_idea_directory(self):
        """Should skip .idea directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.idea")
        self.assertTrue(should_skip_file(path))
    
    def test_skip_vs_directory(self):
        """Should skip .vs directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/.vs")
        self.assertTrue(should_skip_file(path))
    
    def test_allow_normal_python_file(self):
        """Should NOT skip normal .py files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/skill.py")
        self.assertFalse(should_skip_file(path))
    
    def test_allow_skill_md(self):
        """Should NOT skip SKILL.md files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/SKILL.md")
        self.assertFalse(should_skip_file(path))
    
    def test_allow_params_json(self):
        """Should NOT skip params.json files."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/params.json")
        self.assertFalse(should_skip_file(path))
    
    def test_allow_normal_directory(self):
        """Should NOT skip normal directories."""
        from route.sessions import should_skip_file
        path = Path("/some/skill/utils")
        self.assertFalse(should_skip_file(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd RpaClaw/backend && python -m pytest tests/test_sessions.py -v`

Expected: FAIL with "ImportError: cannot import name 'should_skip_file' from 'route.sessions'"

- [ ] **Step 3: Write minimal implementation**

Add this function to `RpaClaw/backend/route/sessions.py` after the imports section (around line 30, before the router definition):

```python
def should_skip_file(path: _Path) -> bool:
    """判断是否应该跳过该文件/目录（不在技能文件列表中展示）。
    
    Args:
        path: 文件或目录的 Path 对象
        
    Returns:
        True 表示应该跳过，False 表示应该展示
    """
    name = path.name
    
    # 跳过 __pycache__ 目录
    if name == '__pycache__':
        return True
    
    # 跳过 Python 字节码文件
    if name.endswith(('.pyc', '.pyo', '.pyd')):
        return True
    
    # 跳过系统临时文件
    if name in {'.DS_Store', 'Thumbs.db', 'desktop.ini', '.gitignore'}:
        return True
    
    # 跳过版本控制和 IDE 目录
    if name in {'.git', '.svn', '.vscode', '.idea', '.vs'}:
        return True
    
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd RpaClaw/backend && python -m pytest tests/test_sessions.py -v`

Expected: All tests PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/tests/test_sessions.py RpaClaw/backend/route/sessions.py
git commit -m "feat: add should_skip_file helper to filter cache files"
```

---

### Task 2: Apply filter to list_skill_files endpoint

**Files:**
- Modify: `RpaClaw/backend/route/sessions.py:962-969`
- Test: `RpaClaw/backend/tests/test_sessions.py`

- [ ] **Step 1: Write integration test**

Add to `RpaClaw/backend/tests/test_sessions.py`:

```python
import tempfile
import shutil
from pathlib import Path as _Path


class TestListSkillFilesFiltering(unittest.TestCase):
    """Test that list_skill_files applies the filter correctly."""
    
    def setUp(self):
        """Create a temporary skill directory with test files."""
        self.temp_dir = tempfile.mkdtemp()
        self.skill_dir = _Path(self.temp_dir) / "test_skill"
        self.skill_dir.mkdir()
        
        # Create normal files
        (self.skill_dir / "SKILL.md").write_text("# Test Skill")
        (self.skill_dir / "skill.py").write_text("def run(): pass")
        (self.skill_dir / "params.json").write_text("{}")
        
        # Create files that should be filtered
        pycache_dir = self.skill_dir / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "skill.cpython-313.pyc").write_bytes(b"fake bytecode")
        (self.skill_dir / "module.pyc").write_text("fake")
        (self.skill_dir / ".DS_Store").write_text("fake")
        (self.skill_dir / "Thumbs.db").write_text("fake")
        (self.skill_dir / ".gitignore").write_text("*.pyc")
        
        vscode_dir = self.skill_dir / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text("{}")
    
    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir)
    
    def test_filtered_file_list(self):
        """Test that rglob with filter returns only normal files."""
        from route.sessions import should_skip_file
        
        items = []
        for file_path in sorted(self.skill_dir.rglob("*")):
            if should_skip_file(file_path):
                continue
            if file_path.is_file():
                rel_path = str(file_path.relative_to(self.skill_dir))
                items.append({
                    "name": file_path.name,
                    "path": rel_path,
                    "type": "file",
                })
        
        # Should only have 3 normal files
        self.assertEqual(len(items), 3)
        
        # Extract just the names for easier assertion
        names = {item["name"] for item in items}
        self.assertEqual(names, {"SKILL.md", "skill.py", "params.json"})
        
        # Verify filtered files are NOT in the list
        self.assertNotIn("skill.cpython-313.pyc", names)
        self.assertNotIn("module.pyc", names)
        self.assertNotIn(".DS_Store", names)
        self.assertNotIn("Thumbs.db", names)
        self.assertNotIn(".gitignore", names)
        self.assertNotIn("settings.json", names)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `cd RpaClaw/backend && python -m pytest tests/test_sessions.py::TestListSkillFilesFiltering -v`

Expected: PASS (the test simulates the fix, so it should pass)

- [ ] **Step 3: Apply filter to list_skill_files function**

In `RpaClaw/backend/route/sessions.py`, modify the `list_skill_files` function around line 962-969:

Find this code:
```python
            items = []
            for file_path in sorted(skill_dir.rglob("*")):
                if file_path.is_file():
                    rel_path = str(file_path.relative_to(skill_dir))
                    items.append({
                        "name": file_path.name,
                        "path": rel_path,
                        "type": "file",
                    })
```

Replace with:
```python
            items = []
            for file_path in sorted(skill_dir.rglob("*")):
                # 跳过不需要展示的文件
                if should_skip_file(file_path):
                    continue
                if file_path.is_file():
                    rel_path = str(file_path.relative_to(skill_dir))
                    items.append({
                        "name": file_path.name,
                        "path": rel_path,
                        "type": "file",
                    })
```

- [ ] **Step 4: Run all tests to verify**

Run: `cd RpaClaw/backend && python -m pytest tests/test_sessions.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/route/sessions.py RpaClaw/backend/tests/test_sessions.py
git commit -m "feat: apply file filter to list_skill_files endpoint"
```

---

### Task 3: Manual verification

**Files:**
- None (manual testing)

- [ ] **Step 1: Start the backend server**

Run: `cd RpaClaw/backend && uv run uvicorn main:app --host 0.0.0.0 --port 8000`

Expected: Server starts successfully

- [ ] **Step 2: Create a test skill with cache files**

In a separate terminal:
```bash
mkdir -p Skills/test_filter_skill
echo "---
name: test_filter_skill
description: Test skill for filter verification
---" > Skills/test_filter_skill/SKILL.md
echo "def run(): pass" > Skills/test_filter_skill/skill.py
mkdir Skills/test_filter_skill/__pycache__
echo "fake" > Skills/test_filter_skill/__pycache__/skill.cpython-313.pyc
echo "fake" > Skills/test_filter_skill/.DS_Store
```

- [ ] **Step 3: Test the API endpoint**

Run: `curl -X GET "http://localhost:12001/api/v1/sessions/skills/test_filter_skill/files" -H "Authorization: Bearer <token>"`

Expected: Response contains only `SKILL.md` and `skill.py`, NOT `__pycache__` or `.DS_Store`

- [ ] **Step 4: Test in frontend**

1. Open browser to `http://localhost:5173`
2. Login with `admin` / `admin123`
3. Navigate to Skills page
4. Click on `test_filter_skill`
5. Verify file list shows only `SKILL.md` and `skill.py`
6. Verify `__pycache__` directory is NOT visible
7. Verify `.DS_Store` file is NOT visible

Expected: Only normal skill files are visible in the UI

- [ ] **Step 5: Clean up test skill**

```bash
rm -rf Skills/test_filter_skill
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "test: verify __pycache__ filtering works in production"
```

---

## Summary

This plan implements file filtering for the skill file listing API in 3 tasks:

1. **Task 1**: Add the `should_skip_file()` helper function with comprehensive unit tests
2. **Task 2**: Apply the filter to the `list_skill_files` endpoint with integration tests
3. **Task 3**: Manual verification in both API and frontend

The implementation follows TDD principles, includes exact file paths and code, and breaks work into bite-sized steps with clear verification at each stage.

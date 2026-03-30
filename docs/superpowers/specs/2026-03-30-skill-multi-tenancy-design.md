# 技能管理多租户架构设计

## 目标

将技能管理从基于文件系统的全局共享架构迁移到基于 MongoDB 的多租户架构。每个用户只能看到和使用自己创建的技能 + 内置技能，支持后端多实例分布式部署。

## 决策记录

- 内置技能（builtin_skills）保持文件系统不变，打包在 Docker 镜像中，所有用户共享
- 用户创建的外部技能全部存储在 MongoDB `skills` 集合中
- 沙箱执行时通过 session 级别路径隔离按需注入技能
- 用户间技能完全隔离，不支持共享
- 现有数据通过一次性迁移脚本导入 MongoDB

---

## Section 1: 数据模型

新建 `skills` 集合，文档结构：

```json
{
  "_id": "ObjectId",
  "user_id": "string",
  "name": "string",
  "description": "string",
  "source": "agent | rpa",
  "blocked": false,
  "files": {
    "SKILL.md": "---\nname: ...\n---\n...",
    "skill.py": "import os\n..."
  },
  "params": {},
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

索引：
- `(user_id, name)` — 唯一索引，保证同一用户下技能名不重复
- `(user_id, blocked)` — 复合索引，加速列表查询

`blocked` 字段内嵌在技能文档中，替代现有的 `blocked_skills` 集合。

---

## Section 2: 后端 API 改造

改造 `sessions.py` 中的技能相关端点，全部改为读写 MongoDB `skills` 集合：

| 端点 | 当前实现 | 改造后 |
|------|---------|--------|
| `GET /sessions/skills` | 扫描文件系统 + 查 blocked_skills | `db.skills.find({user_id})` + 合并内置技能列表 |
| `PUT /sessions/skills/{name}/block` | 操作 blocked_skills 集合 | `db.skills.update_one({user_id, name}, {$set: {blocked}})` |
| `DELETE /sessions/skills/{name}` | `shutil.rmtree()` | `db.skills.delete_one({user_id, name})` |
| `POST /sessions/{sid}/skills/save` | 从沙箱 workspace 复制到 /app/Skills/ | 从沙箱读取文件内容 → `db.skills.insert_one()` |
| `POST /sessions/skills/{name}/read` | 读文件系统 | `db.skills.find_one({user_id, name})` → 返回 `files[filename]` |

内置技能的列表仍然从文件系统扫描（`BUILTIN_SKILLS_DIR`），在返回给前端时标记 `builtin: true`，与用户技能合并返回。

`blocked_skills` 集合在迁移完成后废弃。

---

## Section 3: Agent 执行层改造

### 新建 MongoSkillBackend

替代 `FilteredFilesystemBackend`，实现相同接口（`ls_info`, `read`, `write`, `glob_info`, `grep_raw`）：

- 构造时接收 `user_id`
- 所有操作自动过滤到该用户的未禁用技能
- `read("/skills/skill-name/SKILL.md")` → 查 MongoDB `skills.find_one({user_id, name: "skill-name", blocked: {$ne: true}})` → 返回 `files["SKILL.md"]`
- `ls_info("/skills/")` → 查 MongoDB `skills.find({user_id, blocked: {$ne: true}})` → 返回目录列表
- `write` 操作：agent 保存技能时直接写入 MongoDB

### _build_backend() 改造

- 移除 `FilteredFilesystemBackend` 的使用
- `/skills/` 路由指向 `MongoSkillBackend(user_id=...)`
- `/builtin-skills/` 路由保持 `FilesystemBackend` 不变

### 废弃项

- `get_blocked_skills()` 函数废弃（blocked 状态已内嵌在技能文档中）
- `filtered_backend.py` 文件废弃（被 `MongoSkillBackend` 替代）

---

## Section 4: 沙箱技能注入

沙箱是单容器共享的，多用户同时使用时需要隔离。采用 session 级别路径隔离：

注入路径：`/home/scienceclaw/{session_id}/.skills/{skill_name}/`

流程：
1. Agent 启动时（`_build_backend` 阶段），从 MongoDB 查询用户未禁用的技能
2. 通过 `sandbox_execute_code` 将技能文件写入该 session 的 `.skills/` 目录
3. `eval_skill` 执行时从 session workspace 路径加载

隔离保证：
- 每个 agent session 有独立的技能副本，不会跨 session 干扰
- MCP 调用本身带 `X-Session-ID`，天然隔离
- Session 结束后 workspace 清理时技能副本一起清除

`MongoSkillBackend` 的 `read` 操作直接查 MongoDB（给 agent 看技能列表和内容），实际执行（`eval_skill`）时通过 session workspace 路径加载。

---

## Section 5: RPA 技能导出改造

`SkillExporter.export_skill()` 改为直接写入 MongoDB：

改造点：
- `export_skill` 改为 async 方法
- 接收 `user_id` 参数
- 写入 `db.skills.insert_one()` 而非文件系统
- 调用链：`route/rpa.py` 的 `save_skill` 端点已有 `current_user`，传入即可

```python
await db.skills.insert_one({
    "user_id": user_id,
    "name": skill_name,
    "description": description,
    "source": "rpa",
    "blocked": False,
    "files": {
        "SKILL.md": skill_md_content,
        "skill.py": script_content,
    },
    "params": params,
    "created_at": datetime.now(),
    "updated_at": datetime.now(),
})
```

---

## Section 6: 数据迁移

提供一次性迁移脚本 `scripts/migrate_skills_to_mongo.py`：

1. 扫描 `EXTERNAL_SKILLS_DIR` 下所有技能目录
2. 解析每个技能的 `SKILL.md` 前置元数据和所有文件内容
3. 写入 MongoDB `skills` 集合
4. 迁移 `blocked_skills` 集合中的记录 → 设置对应技能文档的 `blocked: true`

用户归属：现有技能没有 `user_id`。迁移脚本接受 `--default-user-id` 参数，将所有现有技能分配给指定用户。如果只有一个用户，直接查 MongoDB users 集合取第一个。

迁移完成后清理：
- 移除 `docker-compose.yml` 中 `./Skills:/app/Skills` 的 volume 挂载
- 移除 `blocked_skills` 集合的索引初始化代码
- 移除 `config.py` 中 `EXTERNAL_SKILLS_DIR` 配置项

---

## Section 7: 前端适配

前端无感迁移。API 接口语义不变，只是后端实现从文件系统切换到 MongoDB：

- `SkillsPage.vue` — 无需改动，返回数据结构不变
- `ExternalSkillItem` 类型 — 无需改动
- RPA 保存技能 — 无需改动
- `agent.ts` API 客户端 — 无需改动

---

## 影响范围

### 新建文件
- `backend/deepagent/mongo_skill_backend.py` — MongoSkillBackend 实现
- `scripts/migrate_skills_to_mongo.py` — 数据迁移脚本

### 修改文件
- `backend/route/sessions.py` — 技能 CRUD 端点改为操作 MongoDB
- `backend/deepagent/agent.py` — `_build_backend()` 使用 MongoSkillBackend，废弃 `get_blocked_skills()`
- `backend/rpa/skill_exporter.py` — 改为 async，写入 MongoDB
- `backend/route/rpa.py` — 传入 user_id 给 SkillExporter
- `backend/mongodb/db.py` — 添加 skills 集合索引初始化，移除 blocked_skills 索引
- `backend/config.py` — 移除 EXTERNAL_SKILLS_DIR 配置项
- `docker-compose.yml` — 移除 Skills volume 挂载

### 废弃文件
- `backend/deepagent/filtered_backend.py` — 被 MongoSkillBackend 替代

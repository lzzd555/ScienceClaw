# 技能文件编辑器

## Context

技能导出后生成 SKILL.md、skill.py、params.json 三个文件，但目前 SkillDetailPage 只能查看文件，无法编辑。用户希望在页面上直接编辑技能文件。对于 params.json，需支持表单编辑模式（参考 `.claude/params.html` 的 Parameter Editor 卡片设计）和文本编辑模式的切换。

## 方案

在现有 SkillDetailPage 基础上增加编辑能力，而不是新建页面。这样复用已有的文件树 + 文件查看布局，只需：
1. 后端增加文件写入接口
2. 前端 FileViewer 改为可编辑（MonacoEditor 已支持 readOnly=false）
3. 新增 ParamEditor 组件，实现 params.json 的表单编辑模式

### 改动文件

#### 1. 后端：`ScienceClaw/backend/route/sessions.py` — 新增写入接口

在现有 `read_skill_file` 端点后新增：

```python
class WriteSkillFileRequest(BaseModel):
    file: str
    content: str

@router.put("/skills/{skill_name}/write")
async def write_skill_file(skill_name, body, current_user):
    # local mode: 写入文件系统
    # mongo mode: 更新 files.{body.file} 字段
    # 内置技能不可编辑
```

安全：
- 复用已有的 `_Path(settings.external_skills_dir) / skill_name` 路径隔离
- 禁止编辑内置技能（`builtin_skills_dir`）
- 路径遍历防护：`file_path.resolve()` 必须在 `skill_dir` 下

#### 2. 前端 API：`ScienceClaw/frontend/src/api/agent.ts`

新增：
```typescript
export async function writeSkillFile(skillName: string, file: string, content: string)
```

#### 3. 前端：`ScienceClaw/frontend/src/pages/SkillDetailPage.vue` — 增加编辑能力

改动：
- 增加 `editMode` ref，控制查看/编辑状态
- 工具栏增加"编辑"按钮（内置技能禁用）
- 编辑模式下 FileViewer 传 `editable=true`
- 增加保存按钮和 dirty 状态追踪
- 当选中的文件为 `params.json` 时，显示 ParamEditor 组件（带表单/文本模式切换）

#### 4. 前端：`ScienceClaw/frontend/src/components/FileViewer.vue` — 支持编辑

改动：
- 新增 `editable` prop（默认 false）
- `editable=true` 时 MonacoEditor 设置 `readOnly=false`，监听 `@change` 事件 emit 上去
- Markdown 编辑模式用 MonacoEditor（而非 MarkdownFilePreview）

#### 5. 前端：新建 `ScienceClaw/frontend/src/components/ParamEditor.vue` — params.json 表单编辑

参考 `.claude/params.html` 的 Parameter Editor 部分设计：

- 每个参数一张卡片：
  - 普通参数：图标 + 名称 + 类型标签 + "Public" 徽章 + 文本输入框
  - Sensitive 参数：锁图标 + "Secret Value" + "Sensitive" 徽章 + 密码输入框 + "Link Vault" 按钮 + 已关联凭据显示（`border-l-4 border-secondary`）
- "Add Parameter" 按钮
- 删除按钮
- 右上角文本/表单模式切换按钮
- 表单模式：卡片 UI
- 文本模式：MonacoEditor（language=json）
- Props: `content: string`（JSON 字符串），emit `change` 事件

#### 6. i18n：`ScienceClaw/frontend/src/locales/en.ts` + `zh.ts`

新增翻译 key：
- Edit / Save / Cancel / Saving...
- Switch to form/text mode
- Add Parameter / Delete Parameter
- Public / Sensitive / Secret Value / String Value
- Link Vault / Linked to
- Unsaved changes warning
- Built-in skills cannot be edited

## 验证

1. 进入 Skills 页面 → 点击某技能 → 进入 Detail 页面
2. 点击 "编辑" 按钮进入编辑模式
3. 选择 SKILL.md 或 skill.py → MonacoEditor 可编辑 → 保存成功
4. 选择 params.json → 默认展示表单模式（卡片 UI）→ 可切换到文本模式
5. 表单模式下：修改参数值、添加参数、删除参数、关联凭据 → 保存
6. 内置技能的编辑按钮应禁用

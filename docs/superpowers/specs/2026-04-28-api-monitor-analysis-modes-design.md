# API Monitor 分析模式设计

## 背景

API Monitor 页面当前只有一个“分析”动作。这个动作会扫描当前页面的可交互元素，让 LLM 判断哪些元素可以安全探测、哪些需要跳过，然后逐个探测安全元素、捕获 API 请求，并生成 API Monitor MCP 工具定义。

这个流程适合做广泛发现，但当用户已经知道自己想分析的业务操作时，单一自由分析模式不够直接。当前页面还会把 API Monitor MCP 保存成功或失败写入右侧监控日志，这会把持久状态反馈和临时分析日志混在一起。

## 目标

- 保留当前分析行为，并把它作为默认的“自由分析”模式。
- 增加“安全分析”：用户提供自然语言操作目标，系统只执行被判断为安全的页面操作。
- 增加“定向分析”：用户提供自然语言操作目标，业务风险由用户自行把控。系统仍保留平台安全边界，但不替用户拦截提交、删除、注销等业务高风险动作。
- 定向类模式复用 RPA 技能录制过程中的 DOM 快照精简能力，减少 DOM 对模型上下文的压力。
- API Monitor MCP 保存成功或失败时，不再写入监控日志，改为页面 toast/message 提示。
- 为后续更多分析模式保留清晰扩展接口。

## 非目标

- 不重做整个 API Monitor 页面。
- 不替换现有自由分析探测流程。
- 本轮不新增“只读推断分析”模式。
- 不引入持久化的长任务分析模型。
- 除保存反馈方式外，不改变已生成 API Monitor MCP contract 的发布和执行方式。

## 用户体验

顶部操作区从单个“分析”按钮改为“分析”下拉按钮。

下拉菜单包含三种模式：

- 自由分析
  - 默认选项。
  - 使用现有广泛发现流程。
  - 不需要填写操作指令。
- 安全分析
  - 需要用户填写操作指令。
  - 根据指令执行浏览器操作，但启用业务安全过滤。
  - 会跳过删除、注销、支付、不可逆提交、撤销授权等高风险动作。
- 定向分析
  - 需要用户填写操作指令。
  - 根据指令执行浏览器操作，但不做业务风险过滤。
  - 用户负责判断该操作是否可能触发高风险业务后果。

当选择的模式需要指令时，页面在分析下拉按钮附近展示一个紧凑的指令输入框。指令为空时，分析动作不可用。

右侧监控日志继续展示分析生命周期事件、捕获到的 API 调用和生成工具数量。保存 API Monitor MCP 时不再往监控日志追加成功或失败信息，而是：

- 保存成功：显示成功 toast。
- 保存失败：显示错误 toast。
- 名称冲突：继续打开覆盖确认弹窗。

## 前端设计

前端不使用三个硬编码按钮，而是新增统一的模式配置表：

```ts
type AnalysisModeKey = 'free' | 'safe_directed' | 'directed';

interface AnalysisModeOption {
  key: AnalysisModeKey;
  label: string;
  description: string;
  requiresInstruction: boolean;
  riskLevel: 'low' | 'guarded' | 'user_controlled';
}
```

页面状态包括：

- `analysisMode`
- `analysisInstruction`
- `analysisModes`

下拉菜单从 `analysisModes` 渲染。未来新增模式时，应扩展这份配置，而不是继续增加零散按钮和分支。

`analyzeSession` API helper 接受统一 payload：

```ts
interface AnalyzeSessionPayload {
  mode: AnalysisModeKey | string;
  instruction?: string;
}
```

这样既能对现有模式提供类型提示，也允许未来模式先通过字符串接入。

## 后端 API 设计

保留现有接口：

```http
POST /api/v1/api-monitor/session/{session_id}/analyze
```

接口接受可选 JSON 请求体：

```json
{
  "mode": "free",
  "instruction": ""
}
```

兼容规则：

- 空 body 等价于 `mode = "free"`。
- 缺少 `mode` 等价于 `mode = "free"`。
- 未知模式返回 HTTP 400，并给出明确错误信息。
- `safe_directed` 和 `directed` 必须提供非空 `instruction`。

新增请求模型：

```py
AnalysisMode = Literal["free", "safe_directed", "directed"]

class AnalyzeSessionRequest(BaseModel):
    mode: str = "free"
    instruction: str = ""
```

后端使用模式注册表，而不是在 route 中写死多层分支：

```py
ANALYSIS_MODE_REGISTRY = {
    "free": AnalysisModeConfig(handler="free", requires_instruction=False),
    "safe_directed": AnalysisModeConfig(handler="directed", requires_instruction=True, business_safety="guarded"),
    "directed": AnalysisModeConfig(handler="directed", requires_instruction=True, business_safety="user_controlled"),
}
```

route 只负责校验请求，然后把 `mode`、`instruction` 和模型配置传给 manager。

## 后端分析流程

### 自由分析

自由分析保持当前 `analyze_page` 行为：

1. 扫描可交互元素。
2. 让 LLM 分类 safe/skip。
3. 探测 safe 元素。
4. 捕获 API 调用。
5. 基于捕获调用生成工具定义。

该模式必须与当前 API Monitor 页面行为兼容。

### 定向类模式

安全分析和定向分析走新的 manager 分支。该分支应与自由分析分离，但尽量复用已有 helper：

1. 将 session 状态标记为 `analyzing`。
2. 将分析前 capture buffer 中已有请求排入 session history。
3. 使用 RPA 录制同款 snapshot builder 构建当前页面快照。
4. 使用 `compact_recording_snapshot(snapshot, instruction)` 压缩快照。
5. 让 LLM 基于用户指令和精简快照生成简洁的 Playwright 操作计划。
6. 对 `safe_directed`，在执行前分类计划动作，并拒绝业务高风险动作。
7. 对 `directed`，跳过业务风险拒绝，直接执行计划中的浏览器操作。
8. 收集执行过程中捕获到的 API 调用。
9. 使用现有 `_generate_tools_from_calls` 生成工具定义。
10. 通过 SSE 输出生命周期事件。

定向执行必须保留平台安全边界：

- 只允许在当前浏览器 session 内执行浏览器自动化。
- 不执行 shell 命令。
- 不读取或写入本地文件。
- 不授予新的浏览器权限。
- 不允许 LLM 返回任意 Python 代码后无约束执行。

实现上应优先使用结构化 action plan，而不是无约束原始代码。如果复用 RPA 录制 runtime 中的代码执行能力，也必须限制为 Playwright page 操作，并在执行前校验。

## DOM 精简复用

定向类模式复用录制流程中的 DOM 快照精简管线：

- 从当前 Playwright page 构建原始 page snapshot。
- 调用 `compact_recording_snapshot(raw_snapshot, instruction)`。
- 将精简 snapshot 传给定向操作规划 prompt。

精简 snapshot 只应在现有 debug/diagnostic 开关开启时写入诊断信息，默认不写日志。

## SSE 事件

自由分析保留现有事件。

定向类模式可以新增事件，但要继续保留通用的 `progress`、`calls_captured`、`analysis_complete` 和 `analysis_error`：

- `analysis_started`：包含 `mode`、`url` 和是否存在指令。
- `progress`，`step = "snapshot"`：构建和压缩 DOM 时发送。
- `progress`，`step = "planning"`：生成操作计划时发送。
- `directed_plan_ready`：包含动作数量摘要和 guarded/user-controlled 模式。
- `directed_action_skipped`：安全分析拒绝某个动作时发送。
- `progress`，`step = "executing"`：执行动作时发送。
- `calls_captured`：执行结束并收集 API 调用后发送。
- `analysis_complete`：包含 `mode`、`tools_generated` 和 `total_calls`。

前端应忽略未知 SSE 事件，除非它有专门展示逻辑。这样未来事件可以增量添加。

## MCP 保存反馈

发布流程保持不变，只改前端反馈方式：

- 成功后移除 `addLog("INFO", "已保存 MCP ...")`。
- 普通失败后移除 `addLog("ERROR", "保存 MCP 失败...")`。
- 使用 `showSuccessToast` 和 `showErrorToast`。
- 覆盖冲突继续弹窗。等待覆盖确认这类过程性提示可以保留在日志中，但最终保存结果必须只走 toast/message。

## 扩展性

未来新增分析模式时，通过注册新模式配置接入：

- 前端：扩展 `ANALYSIS_MODES`。
- API payload：继续复用 `mode` 和 `instruction`。
- 后端：增加 registry entry 和 handler。
- SSE：继续发送通用生命周期事件，并按需添加模式专属事件。

潜在后续模式：

- `readonly_directed`：不点击、不提交，只推断候选 API。
- `schema_only`：只基于已捕获调用推断请求/响应 schema。
- `auth_flow_analysis`：专注发现 token 与 credential 流程。
- `recorded_flow_replay`：重放已保存操作路径并捕获 API 调用。

## 测试计划

后端测试：

- 空 analyze body 默认进入自由分析。
- 自由分析仍调用现有自由分析路径。
- 未知模式返回 400。
- 定向类模式要求非空指令。
- 安全分析把 guarded 业务安全设置传给定向 handler。
- 定向分析把 user-controlled 业务安全设置传给定向 handler。
- 定向类模式在规划前使用精简 snapshot。

前端测试：

- 分析下拉菜单从模式配置渲染所有模式。
- 只有需要指令的模式才显示指令输入框。
- 定向类模式在指令为空时禁用分析动作。
- `analyzeSession` 发送 `{ mode, instruction }`。
- MCP 发布成功触发成功 toast，且不追加监控日志。
- MCP 发布失败触发错误 toast，且不追加监控日志。
- MCP 发布名称冲突仍打开覆盖确认弹窗。

## 待定事项

无。本轮实现应交付三种模式：自由分析、安全分析、定向分析，并以分析下拉框作为后续模式扩展入口。

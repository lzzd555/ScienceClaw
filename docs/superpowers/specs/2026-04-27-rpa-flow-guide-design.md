# RPA 技能录制流程向导设计

## 背景

当前 RPA 技能录制流程分散在三个页面：

- `/rpa/recorder`：录制浏览器操作和 AI 辅助步骤。
- `/rpa/configure?sessionId=...`：修复录制步骤、配置参数、预览脚本。
- `/rpa/test?sessionId=...`：执行回放测试并保存技能。

页面之间主要依靠局部按钮跳转。用户完成录制后进入配置页，再想重新录制时缺少稳定、显眼的返回入口；配置、测试、保存之间的流程状态也不够统一。

本设计只优化前端流程导航和页面组织，不改变 RPA trace-first 录制主路径、后端 session 生命周期、脚本生成、测试执行或技能导出逻辑。

## Stitch 参考

已使用 Stitch 生成一张参考设计：

- Project: `RpaClaw Current UI Design System - Gemini 3.1 Pro`
- Screen: `RPA Skill Configuration - Optimization Concept`
- Local screenshot: `.stitch/designs/rpa-flow-guide-configure.png`
- Local HTML: `.stitch/designs/rpa-flow-guide-configure.html`

可吸收的设计点：

- 顶部使用 56-64px 的窄流程条承载步骤状态和关键动作。
- 流程步骤为 `Record -> Configure -> Test & Save`，当前步骤突出显示，前序步骤可点击。
- 配置页可以在顶部同时看到录制步数、待修复状态和测试门禁。
- 主体继续保持工具型工作台布局，避免 landing page 或大 hero 式改造。

不直接采用的点：

- 不把配置表单浮在浏览器预览上；现有配置页以步骤列表和右侧设置面板为主，保留当前信息架构更稳妥。
- 不更换主色系；继续使用现有 violet/magenta RPA accent，并用中性 surface 控制整体观感。

## 推荐方案

新增共享组件 `RpaFlowGuide.vue`，由录制、配置、测试三个页面统一使用。

组件职责：

- 显示三步流程：`录制`、`配置`、`测试保存`。
- 显示每步状态：active、completed、available、disabled。
- 提供跨步骤导航：回到配置、进入测试、重新录制。
- 将“首页”“技能库”等全局动作和当前页面主动作组织在同一窄顶栏。
- 对重新录制执行确认提示，避免用户误丢工作。

不采用每页复制一段流程条，因为会让状态判断和跳转逻辑继续分散。也不新增 `/rpa/workflow` 壳路由，因为录制页和测试页有 WebSocket、screencast、自动启动 session 等生命周期副作用，当前阶段重构风险高于收益。

## 用户流程

### 录制页

- 顶部当前步骤为 `录制`。
- `配置` 在存在 `sessionId` 后可用。
- 点击 `配置` 复用当前 `stopRecording` 行为，停止当前录制 session 并跳转 `/rpa/configure?sessionId=...`。
- `测试保存` 在录制页禁用，提示需要先完成配置。
- 主动作保留为 `完成录制`。

### 配置页

- 顶部当前步骤为 `配置`。
- `录制` 可点击，但表示“重新开始”，不是继续当前 session。
- 点击 `录制` 时必须弹出确认：
  - 明确说明重新录制会丢弃当前录制步骤、参数配置、脚本预览和测试结果。
  - 用户确认后跳转 `/rpa/recorder`，由录制页创建全新 session。
  - 用户取消则留在当前页，不改变 session。
- `测试保存` 在没有待修复 diagnostics 时可用，跳转 `/rpa/test?sessionId=...` 并携带现有 skill metadata 和 params。
- 如果存在 diagnostics，`测试保存` 禁用，并显示待修复数量。
- 主动作保留为 `开始测试`，辅助动作保留 `预览脚本`、`转换为 MCP 工具`。

### 测试页

- 顶部当前步骤为 `测试保存`。
- `配置` 可点击，返回 `/rpa/configure?sessionId=...`。
- `录制` 可点击，但同样是重新开始，必须展示丢弃确认。
- 测试通过后主动作是 `保存技能`。
- 测试失败时主动作是 `重新执行`，并保留返回配置入口。
- 保存成功后的跳转技能库逻辑不变。

## 状态规则

组件由页面显式传入状态，不在组件内部请求后端：

- `currentStep`: `record`、`configure`、`test`。
- `sessionId`: 当前 RPA session id，可为空。
- `recordedStepCount`: 已录制或已配置的步骤数量。
- `diagnosticCount`: 待修复步骤数量。
- `isRecording`: 录制页计时状态。
- `recordingTime`: 录制页计时文本。
- `testState`: `idle`、`running`、`success`、`failed`。
- `skillName`: 测试页或配置页的技能名称。

步骤可用性：

- `record` 始终可点击，但在配置页和测试页点击时必须先确认丢弃。
- `configure` 需要 `sessionId`。
- `test` 需要 `sessionId` 且 `diagnosticCount === 0`。

组件只发出事件或调用传入回调，避免直接耦合页面内部的停止录制、生成参数、保存技能逻辑。

## 视觉设计

顶栏高度控制在 56-64px，使用 sticky 或 fixed 取决于页面现有布局：

- 录制页和测试页当前是 `h-screen flex-col`，顶栏作为第一行。
- 配置页当前顶栏 sticky，替换为同样 sticky 的流程向导。

视觉原则：

- 中性背景为主：`#F8F9FB`、`#F2F4F6`、`#FFFFFF`。
- 当前步骤使用 violet/magenta accent，已完成步骤使用低饱和状态色。
- 禁用步骤使用灰色文本和轻量说明，不用强警告大块占位。
- 按钮使用 8-12px radius，避免过多 rounded-full。
- 右侧主动作视觉最强，导航类动作保持轻量。
- 文案紧凑，不添加“如何使用”式说明段落。

## 组件接口草案

```ts
type RpaFlowStep = 'record' | 'configure' | 'test';
type RpaTestState = 'idle' | 'running' | 'success' | 'failed';

interface RpaFlowGuideProps {
  currentStep: RpaFlowStep;
  sessionId?: string | null;
  recordedStepCount?: number;
  diagnosticCount?: number;
  isRecording?: boolean;
  recordingTime?: string;
  testState?: RpaTestState;
  skillName?: string;
}
```

事件：

- `go-record`: 用户确认重新录制后触发，页面跳转 `/rpa/recorder`。
- `go-configure`: 页面决定如何跳转或先停止录制。
- `go-test`: 页面决定如何构建 test query。
- `primary-action`: 当前页主动作。
- `secondary-action`: 可选，供预览脚本、转换 MCP 工具等按钮使用。

实现时可以根据现有 Vue 风格调整为 props + named slots，避免组件接口过度抽象。

## 错误与门禁

重新录制确认是本次设计的关键安全点。确认框必须满足：

- 只在离开配置页或测试页去 `/rpa/recorder` 时出现。
- 文案明确“会丢弃之前的所有工作”。
- 默认取消，不自动开始新录制。
- 确认后只跳转新录制页，不尝试删除旧 session；旧 session 是否清理由现有后端生命周期处理。

测试门禁：

- 如果 `diagnosticCount > 0`，流程条里的 `测试保存` 禁用。
- 禁用原因显示为简短 chip，例如 `3 个步骤待修复`。
- 点击禁用态不跳转，可在页面现有 error 区域提示“修复后才能开始测试”。

## 测试策略

前端单元测试优先覆盖共享状态逻辑：

- 录制页：有 session 后配置步骤可用，测试步骤不可用。
- 配置页：有 diagnostics 时测试步骤禁用。
- 配置页：点击录制会先请求确认，确认后才导航 `/rpa/recorder`。
- 测试页：配置步骤保留当前 sessionId，录制步骤走丢弃确认。

构建验证：

- 运行前端已有 test/build 命令，至少覆盖新组件和受影响 RPA 页面。
- 如测试环境无法直接覆盖 Vue 页面交互，使用 `npm run build` 作为最低验证，并人工检查三页导航。

## 非目标

- 不改变后端 RPA session API。
- 不改变 trace-first 录制策略。
- 不实现“继续当前 session 录制”。
- 不新增工作流壳路由。
- 不重新设计配置页全部表单、测试日志或 AI 助手。

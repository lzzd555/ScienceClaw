# API Monitor 置信度与采用状态设计

## 目的

API Monitor 的目标是把网页里的网络请求整理成可用的 MCP 工具，同时避免注入脚本、埋点、配置查询、后台轮询等请求污染最终工具列表。

当前采集链路已经能过滤静态资源、非 XHR/fetch 请求、跨 origin 请求，并且去重时只按 URL path 判断重复。但这些规则无法处理同源注入请求。例如，网页自身的业务 API 和注入代码发出的同源配置查询，可能拥有相同 host、referer、cookie 和 fetch 元数据。仅靠 URL 和 header 不能稳定区分它们。

本设计采用可逆流程：保留候选 API，为每个候选项附加置信度和证据，高置信项默认采用，最终只发布用户采用的 API。

## 目标

- 保留所有采集到的 API 候选项，避免误判后无法找回。
- 为每个生成的 API 候选项标注 `confidence`、`confidence_reasons` 和 `selected`。
- 只有高置信业务 API 默认 `selected=true`。
- 用户可以手动切换每个候选 API 是否采用。
- 发布 MCP 工具时只发布已采用的候选 API。
- 对同源注入请求，使用请求来源归因，而不是只依赖 URL/header 启发式规则。

## 非目标

- 第一版不在采集阶段永久删除低置信请求。
- 第一版不做用户训练规则系统。
- 不依赖 LLM 做实时请求过滤。
- 不因为复杂后处理阻塞录制或分析流程。

## 推荐方案

使用候选项管理模型：

```text
CapturedApiCall -> 分组后的 API 候选项 -> 置信度分类 -> 用户选择 -> MCP 发布
```

每个候选 API 包含：

```text
confidence: high | medium | low
selected: boolean
confidence_reasons: string[]
source_evidence:
  initiator_type
  initiator_urls
  js_stack_urls
  frame_url
  action_window_matched
```

默认采用规则：

```text
high   -> selected = true
medium -> selected = false
low    -> selected = false
```

保守原则是：不确定的请求可见，但默认不采用。

## 请求来源证据

### CDP Initiator

为每个被监控页面订阅 Chrome DevTools Protocol 的 `Network.requestWillBeSent` 事件。按请求身份保存 initiator 元数据，包括：

- `initiator.type`
- 调用栈中的脚本 URL
- 可用时保存 function name、line number、column number
- 请求时间戳

这些信息用于判断请求是由页面脚本、注入脚本、浏览器扩展、eval 类来源，还是未知浏览器行为触发。

### 页面级 Fetch/XHR Stack

在页面脚本运行前注入一段轻量脚本，包装：

- `window.fetch`
- `XMLHttpRequest.prototype.open`
- `XMLHttpRequest.prototype.send`

每次 fetch/XHR 记录：

- method
- URL
- timestamp
- `Error().stack`
- 当前 frame URL

随后用 method、URL 和一个很小的时间窗口，把这条 JS 层记录与实际捕获到的请求关联起来。它用于补充 CDP initiator 不完整的情况。

### 动作时间窗口

自动探测和手动录制时，记录当前用户动作或探测动作的时间戳。请求如果发生在动作后的短窗口内，初始建议为 0-2 秒，就获得一个正向置信度信号。窗口外请求不会被直接丢弃，但除非有其他强证据证明是业务 API，否则会被降权。

## 置信度分类

第一版使用可解释的规则分类，不使用黑盒判断。

高置信：

- 同 origin，或被现有采集规则明确允许。
- 发生在用户动作或自动探测动作窗口内。
- CDP initiator 或 JS stack 指向当前页面业务脚本。
- URL 路径或响应结构像业务数据，例如 `/api`、`/biz`、`/v1`、列表、详情、状态类响应。

中置信：

- 同 origin，并且匹配动作时间窗口。
- initiator 或 JS stack 缺失、不完整。
- 请求和响应看起来像业务请求，但来源证据不足。

低置信：

- initiator 或 stack 指向 extension、userscript、injected script、eval、通用 SDK 或无关 frame。
- 路径像配置、埋点、日志、心跳、追踪、模型别名探测等请求。
- 请求周期性出现，或明显不在动作窗口内。

每个候选 API 保存简短的用户可读原因，例如：

- `由用户动作触发`
- `由页面业务脚本发起`
- `缺少 initiator 调用栈`
- `路径疑似配置查询`
- `不在动作时间窗口内`

## 数据模型变化

在采集请求或分组后的工具定义上扩展：

```text
confidence: high | medium | low
selected: bool
confidence_reasons: list[str]
source_evidence: dict
```

推荐把这些字段放在分组后的 API 候选项或工具定义层级，因为用户决定是否采用的是一个接口候选项，而不是单条原始请求样本。原始请求样本仍然可以保存证据，用于调试和评分。

## 后端流程

### 采集

1. 保留现有网络过滤规则。
2. 捕获过滤后剩余的 XHR/fetch 请求。
3. 可用时附加 CDP initiator 证据。
4. 可用时附加页面级 fetch/XHR stack 证据。
5. 附加动作时间窗口元数据。

### 候选项生成

1. 使用现有 dedup key 对采集请求分组。
2. 生成候选工具定义。
3. 根据每组样本和证据计算置信度。
4. 根据置信度设置默认 `selected`。
5. 把候选项、原因和证据摘要返回前端。

### 采用状态更新

新增一个后端接口，用于更新候选项采用状态：

```text
PATCH /api/v1/api-monitor/session/{session_id}/tools/{tool_id}/selection
body: { selected: boolean }
```

该接口把用户选择保存到 session 的工具定义中，避免刷新页面后丢失状态。

### MCP 发布

发布 API Monitor MCP 工具时，只包含：

```text
selected == true
```

中置信和低置信候选项只有在用户手动采用后才会进入 MCP。

## 前端流程

API Monitor 页面把候选项分为两组：

```text
采用
不采用
```

每条 API 展示：

- method
- URL pattern
- confidence 标签
- 简短置信度原因
- 采用状态开关

默认布局：

- 高置信候选项显示在“采用”组。
- 中置信和低置信候选项显示在“不采用”组。
- 用户可以通过开关在两组之间移动任意 API。

UI 文案避免使用破坏性表达。低置信 API 不是“删除”，只是“未采用”。

## 错误处理

- 如果 CDP initiator 捕获失败，继续录制，并把来源证据标记为不可用。
- 如果 fetch/XHR stack 注入失败，继续使用 CDP 和启发式信号。
- 如果置信度评分无法分类候选项，标记为 `medium` 且 `selected=false`。
- 如果采用状态更新失败，前端保持当前 UI 状态不变，并显示普通 API 错误。

## 性能

该设计不增加额外网络请求，也不在采集分类阶段调用 LLM。CDP 事件和 JS stack 都是在请求发生时顺手记录。主要开销是元数据存储和轻量规则评分。

第一版可以保持现有探测等待时间不变。动作时间窗口使用已有时序，不额外增加等待。

## 测试

后端测试：

- 同源业务请求可以被标记为高置信，并默认采用。
- 同源注入或配置类请求可以被标记为低置信，并默认不采用。
- 缺少 initiator 证据时生成中置信，并默认不采用。
- 用户采用状态更新可以持久保存。
- MCP 发布只包含已采用候选项。
- 现有 URL 去重、origin 过滤和静态资源过滤继续通过。

前端测试：

- 候选 API 分别渲染到“采用”和“不采用”分组。
- 正确显示置信度标签和原因。
- 切换采用状态会调用更新接口。
- 发布流程排除未采用候选项。

## 实施顺序

1. 添加数据字段和后端评分 helper。
2. 添加 CDP initiator 捕获。
3. 添加页面 fetch/XHR stack 捕获和关联逻辑。
4. 在候选项生成时应用置信度评分。
5. 添加采用状态更新接口。
6. 更新 MCP 发布过滤逻辑。
7. 更新前端候选项分组和开关。
8. 补充分类、选择和发布过滤测试。

## 后续扩展

后续版本可以加入用户自定义规则：

- 总是采用匹配某个 URL pattern 的请求。
- 永不采用匹配某个 URL pattern 的请求。
- 信任某些脚本 bundle。
- 忽略某些 initiator URL。

这部分应在候选项采用流程稳定后再实现。

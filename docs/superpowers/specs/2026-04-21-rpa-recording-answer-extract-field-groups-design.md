# RPA Recording Assistant Answer/Extract Intent And Field Group Design

日期: 2026-04-21

## 目标

改进技能录制中的 AI 助手，使其同时解决两类问题：

1. 用户只是想“询问页面内容”时，LLM 能在意图识别层正确识别为问答，而不是被迫归类成点击、提取或其他动作。
2. 在复杂表单 DOM 下，系统能更稳定地把字段名和值关联起来，例如将“期望完成时间”和“2025-06-13”识别为同一字段对，并在需要时把值写入现有上下文。

## 已确认的产品行为

- 意图识别要从 LLM 层开始，不依赖后置关键词修补作为主判断。
- 用户请求可以同时包含“问答”和“提取”两个子意图。
- 问答型结果只返回给用户，不计入录制步骤。
- 提取型结果计入录制步骤，并继续沿用当前项目中已有的上下文写入方式，即 `result_key -> value`。
- 对复杂 DOM 的字段识别采用字段块归并方案。
- 当字段块归并命中但其锚点校验失败时，默认回退到原始 `content_nodes` 再尝试一次。

## 问题总结

### 1. 录制助手缺少稳定的“问答”意图出口

当前 `RpaClaw/backend/rpa/assistant.py` 的 `SYSTEM_PROMPT` 主要围绕原子浏览器动作组织，结构化动作集合包括 `navigate|click|fill|extract_text|press`。这意味着当用户说“帮我看看期望完成时间是什么”时，LLM 没有一个合法、稳定的“只回答页面内容”的输出类型，只能勉强把请求塞进已有动作。

同时，当前上下文写入规则又主要依赖“提取/读取/获取/总结/记录”等关键词判断显式抽取请求。这种逻辑适合做兜底，不适合作为主判据，会进一步放大问答与提取的混淆。

### 2. 复杂 DOM 下字段名和值的关联信号太弱

当前 `SNAPSHOT_V2_JS` 已经会抓取 `content_nodes`，并尝试给部分节点补充 `field_name`。但这部分逻辑主要依赖：

- 前一个兄弟节点文本
- 父节点内带 `label` 类名的元素

对于 AUI 等复杂表单组件，字段名和值往往位于同一 `form-item` 容器的不同深层后代节点，中间隔着多层包装元素。结果是系统常常只能捕获值节点文本，却无法稳定恢复“这个值属于哪个字段”。

## 方案对比

### 方案 A：最小改动

- 在 LLM 层新增 `answer` 意图
- 保留现有 `extract_text` 逻辑
- 仅继续增强 `content_nodes.field_name` 的启发式规则

优点：
- 改动小
- 上线快

缺点：
- 对复杂表单的收益有限
- 字段匹配仍会持续依赖零散规则补丁

### 方案 B：推荐方案

- 在 LLM 层新增 `answer` 和 `extract_text` 双意图，并允许一条请求拆成多子意图
- 在快照层新增 `field_groups`
- `extract_text` 优先匹配 `field_groups`
- `field_groups` 锚点校验失败时，回退到原始 `content_nodes`

优点：
- 兼容现有 locator 和代码生成体系
- 同时解决问答与复杂字段提取
- 失败时有稳定回退路径

缺点：
- 需要扩展快照结构和提取解析逻辑

### 方案 C：激进 DOM 精简

- 大幅裁剪 DOM/快照，只保留提取专用结构
- LLM 和提取逻辑主要依赖精简后的结果

优点：
- 输入更短
- 理论上利于模型聚焦

缺点：
- 侵入性高
- 容易误删后续动作、定位、调试需要的线索
- 对现有通用录制架构影响过大

## 结论

采用方案 B。

核心原则：

- 原始快照保留，不做全局替换型 DOM 精简。
- 新增字段块归并层，作为提取专用的结构化视图。
- 代码执行仍落到原始节点定位器上，不直接执行“字段块”。

## 设计一：LLM 双意图识别

### 目标

让录制助手在第一层就能区分：

- `answer`：查询并回答页面内容
- `extract_text`：提取并沉淀为录制步骤/上下文

### 输出模型

当前结构化动作协议从“只有动作”扩展为“问答 + 动作”的统一协议。允许返回：

- 单个 JSON 对象
- 多个 JSON 对象组成的数组

新增 `answer` 动作类型，语义为“读取页面信息并返回自然语言答案，不记录步骤”。

推荐结构：

```json
{
  "action": "answer|extract_text|navigate|click|fill|press",
  "description": "short summary",
  "prompt": "original user instruction",
  "result_key": "expected_completion_date",
  "target_hint": {
    "name": "期望完成时间"
  }
}
```

### 行为规则

- 当用户只是询问页面内容时，优先输出 `answer`。
- 当用户明确要求提取、保存、后续使用、作为参数时，输出 `extract_text`。
- 当一句话同时包含问答和提取时，允许同时输出 `answer` 与 `extract_text`。
- `answer` 的执行结果会拼入给用户的回复，但不会写入步骤，也不会进入 `context_writes`。
- `extract_text` 继续按现有方式生成步骤，并依据 `result_key` 把值写入上下文。

### 为什么不只靠后处理关键词

关键词判断只能作为保底辅助，不能作为主判据。真正的分流应在 LLM 输出层完成，否则模型依旧会把问答错误地压成其他动作，后端只能被动修补。

## 设计二：字段块归并层 `field_groups`

### 目标

在不替换现有快照结构的前提下，为复杂表单场景增加一层“字段名 + 字段值 + 可执行锚点”的解释视图。

### 为什么不是简单的“精简 DOM”

字段块归并层与全局精简 DOM 的关键差异是：

- 精简 DOM 会替换或强依赖裁剪后的输入，风险来自误删信号。
- `field_groups` 是从原始 DOM 派生出来的额外结构，原始 `content_nodes / containers / actionable_nodes` 全部保留。

因此：

- 归并错了可以回退
- 调试时可以追溯到原始节点
- 不会破坏其他动作解析和定位能力

### 数据结构

推荐每个 `field_group` 至少包含：

```json
{
  "field_name": "期望完成时间 (UTC+08:00)",
  "field_value": "2025-06-13",
  "container_id": "container-4",
  "label_node_id": "content-12",
  "value_node_id": "content-18",
  "label_locator": { "method": "text", "value": "期望完成时间 (UTC+08:00)" },
  "value_locator": { "method": "css", "value": "[data-field='expectedCompletionDate']" },
  "confidence": 0.92
}
```

设计要求：

- 不能只保存纯文本配对
- 必须保留原始节点引用与可执行 locator
- 代码生成和运行时执行仍以 `value_locator` / `value_node_id` 为准

### 归并策略

字段块归并优先在“字段容器”内部进行，不做全局跨页配对。优先识别这些模式：

- `form`、`section`、`field-panel`、`aui-form-item`、`collapse-item` 等明显字段容器
- 容器内的 `label`、`.label`、`.field-header`、`legend` 作为字段名候选
- 容器内的 `.aui-input-display-only__content`、`.display-only`、`[data-field]`、只读/禁用输入框值作为字段值候选
- 当值节点自带更稳定的 selector（例如 `data-field`）时，优先把它作为 `value_locator`

### 在给定 DOM 中的目标结果

对于用户提供的 AUI 表单结构，系统应优先归并出：

- `field_name = 期望完成时间 (UTC+08:00)`
- `field_value = 2025-06-13`
- `value_locator = [data-field='expectedCompletionDate']` 或同等稳定的值节点定位器

而不是在整页内容节点里单独命中日期文本。

## 设计三：提取执行与回退

### 解析顺序

`extract_text` 的匹配顺序调整为：

1. 优先在 `field_groups` 中按 `target_hint.name` 查找匹配字段
2. 命中后使用 `value_locator` 或 `value_node_id` 作为执行锚点
3. 运行时校验锚点是否有效
4. 如果校验失败，则回退到原始 `content_nodes`
5. 若回退后仍失败，再返回本次提取失败

### 回退策略

已确认采用回退策略 1：

- 字段块命中但锚点校验失败时，回退到原始 `content_nodes`

不直接终止，也不立即再次请求 LLM 重判。

### 校验策略

校验保持保守：

- 读取 `value_locator` 的当前文本
- 如果为空、明显不符、或执行报错，则认为字段块锚点无效

这一步用于避免“字段归并成功但 locator 失效”时把错误值写入上下文。

## 设计四：步骤、上下文与前端表现

### `answer` 的持久化策略

- 不生成录制步骤
- 不触发 `context_writes`
- 只用于即时回答用户

### `extract_text` 的持久化策略

- 生成标准录制步骤
- 保持现有 `result_key -> value` 的上下文写入方式
- 不额外改变 ledger 数据结构

### 混合请求的结果组织

当一条请求同时包含 `answer` 和 `extract_text` 时：

- 用户看到自然语言答案
- 录制步骤中只出现 `extract_text`
- 上下文只记录提取出的结果

## 架构影响

这是一次增量扩展，不是重构式替换。

当前链路：

- 页面快照产出 `actionable_nodes / content_nodes / containers`
- 提取逻辑从 `content_nodes` 中选目标
- `extract_text` 执行 locator 并返回文本

扩展后链路：

- 页面快照产出 `actionable_nodes / content_nodes / containers / field_groups`
- LLM 产出 `answer` 或 `extract_text`
- `extract_text` 优先匹配 `field_groups`
- `field_groups` 校验失败时回退到 `content_nodes`
- 执行仍使用原始 locator，不直接执行字段块对象

## 涉及文件

- `RpaClaw/backend/rpa/assistant.py`
  - 扩展 `SYSTEM_PROMPT`
  - 支持 `answer` 动作
  - 支持单轮多意图混合执行
  - 确保 `answer` 不写步骤和上下文

- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
  - 在 `SNAPSHOT_V2_JS` 中新增 `field_groups`
  - 实现字段容器内的 label/value 归并逻辑

- `RpaClaw/backend/rpa/assistant_runtime.py`
  - 扩展意图解析与执行
  - `extract_text` 优先使用 `field_groups`
  - 增加字段块锚点校验与回退逻辑

- 测试文件
  - 补充问答/提取双意图场景
  - 补充复杂 DOM 字段块归并
  - 补充字段块失效回退

## 测试要点

至少覆盖以下场景：

1. 普通问答
   - 输入：“帮我看下期望完成时间是什么”
   - 期望：返回答案，不新增步骤，不写上下文

2. 明确提取
   - 输入：“提取期望完成时间，保存成参数 expected_completion_date”
   - 期望：新增 `extract_text` 步骤，并把值写入上下文

3. 混合请求
   - 输入：“看看期望完成时间是什么，并保存成参数”
   - 期望：返回自然语言答案，同时只记录一条提取步骤

4. 复杂 AUI 表单字段
   - 期望：优先命中 `field_groups`
   - `field_name` 与 `field_value` 正确配对

5. 字段块锚点失效
   - 人为构造 `value_locator` 失败
   - 期望：自动回退到原始 `content_nodes`

## 风险与约束

- `field_groups` 归并规则如果写得过于激进，可能把相邻展示文本误归并成字段对，因此必须保留 `confidence` 和回退路径。
- `answer` 引入后，需要避免它被误计入步骤，否则会污染导出脚本。
- 混合多意图执行需要明确前端返回文本与后端步骤落库的分离边界。

## 非目标

本设计不包含以下内容：

- 修改上下文 ledger 的数据结构
- 引入新的参数对象持久化格式
- 对全局快照做破坏式 DOM 精简
- 让代码生成直接依赖纯文本字段摘要而不依赖 locator

## 实施顺序

1. 扩展 LLM 协议，新增 `answer` 并支持混合多意图
2. 在快照层新增 `field_groups`
3. 让 `extract_text` 优先消费 `field_groups`
4. 增加字段块锚点校验与 `content_nodes` 回退
5. 补全回归测试

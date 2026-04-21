# RPA Session Context Service Design

日期: 2026-04-21

## 概述

当前 RPA 技能录制已经引入 `context_ledger`、`context_reads/context_writes` 和生成态 `rebuild_context()`，但上下文读写仍分散在多个路径中：

- 录制态 `ai_script` 运行前会临时从 ledger 构建 `context` dict
- 结构化动作通过 `context_reads` 间接驱动生成器注入 `context.get(...)`
- `answer` 仍主要按页面抽取语义执行，不理解“查询当前上下文”这一类请求
- generator 在合同缺失时仍可能退回旧的 `context:buyer` 占位字符串语义

这导致录制态推理、录制态执行、生成态回放虽然都“涉及 context”，但并没有共享同一个明确的上下文服务层。结果就是：

1. 录制时 LLM 能推断出应该存在哪些 key，但不一定能读取到统一的当前上下文视图
2. 导出脚本在部分路径里无法稳定遵守 `context.get(...)` 契约
3. 上下文缺失会在不同动作类型里以不同形式退化，后续很容易反复回归

本设计保留 `context_ledger` 作为事实源，引入一个显式的 `SessionContextService` 作为唯一上下文语义层。所有录制态和生成态的上下文读写，都统一通过这层完成。

---

## 目标

本设计需要满足以下目标：

- 恢复录制态和生成态通过同一上下文命名空间连接的能力
- 让 `context_ledger` 成为唯一事实源，而不是多个路径各自拼接上下文
- 让 `ai_script`、结构化动作、`answer` 查询共享同一套上下文读取规则
- 让 step 级别的 `context_reads/context_writes` 成为正式合同，而不是脆弱的旁路提示
- 让 generator 严格消费上下文合同生成 `rebuild_context()`，不再依赖旧的 placeholder 语义
- 提供一套显式错误处理与测试约束，防止未来在新动作类型中再次出现同类问题

---

## 非目标

本设计不包含以下内容：

- 重写整个 RPA assistant 或录制架构
- 替换现有 `TaskContextLedger` 数据模型为完全不同的持久化系统
- 引入新的 DSL 来表达全部动作或流程控制
- 在本轮里彻底删除所有历史数据兼容路径
- 为所有自然语言回答自动生成新的上下文字段

---

## 问题总结

### 当前断裂点

当前实现里至少存在三个关键断裂点。

第一，`context_ledger` 有事实条目，但没有被稳定补全成“当前 session 的完整 context dict”，只有 `ai_script` 在执行前会临时构建一份投影。其他路径并不共享这份视图。

第二，上下文读取语义没有统一。`ai_script`、结构化 `fill`、`answer` 各自依赖不同的读取机制，导致同一个上下文字段在不同动作类型里行为不一致。

第三，ledger 写入、step 合同、generator 输出三段没有完全闭环。部分路径在合同缺失时仍会退回旧式 placeholder 逻辑，例如 `kwargs.get('textbox', 'context:buyer')`。

### 直接症状

这些断裂点会直接表现为：

- LLM 能说出应该存在 `buyer`、`department` 等 key，但不能确认它们的具体值
- “当前上下文中有哪些内容”这类问题会误走页面抽取语义，而不是 session context 查询
- 导出脚本对上下文的消费方式不稳定，录制态和生成态重新分叉

### 根因

根因不是单一函数缺参数，而是系统缺少一个正式的 session 级上下文语义层。

在当前实现中：

- `context_ledger` 是事实源
- `step.context_reads/context_writes` 是行为记录
- `generator.rebuild_context()` 是生成态重建机制

但没有一个统一的服务来保证这三者在录制态和生成态中始终围绕同一份“当前上下文视图”运作。

---

## 备选方案

### 方案 1：最小修补

在现有实现上补 prompt 中的 context snapshot，并为 `answer` 增加一个特殊分支。

优点：

- 实现成本最低
- 能较快改善“回答当前上下文”这类场景

缺点：

- 读取逻辑仍然分散
- generator 和 step contract 仍然容易漂移
- 未来新增动作时仍可能绕开统一上下文语义

### 方案 2：中等修复

不新增服务对象，只在 `assistant.py` 中补一组统一 helper，集中处理 context build、read、write 和 answer 查询。

优点：

- 改动中等
- 可以一定程度统一录制态逻辑

缺点：

- 上下文语义仍然依附在 assistant 模块里
- generator、manager、assistant 之间的边界依然模糊
- 长期维护时仍有再次分叉风险

### 方案 3：Session Context Service

保留 `context_ledger` 作为持久事实源，引入 `SessionContextService` 作为唯一上下文语义层。录制态推理、录制态执行、生成态导出统一消费该服务。

优点：

- 录制态和生成态的上下文边界最清晰
- 所有读写入口有单一约束点
- 更适合增加测试与回归保护
- 最符合“彻底修复，不再反复回归”的目标

缺点：

- 需要跨 assistant、generator、manager、tests 协同改动

### 结论

选择方案 3。

---

## 核心设计

### 设计原则

完整修复遵循以下原则：

- `context_ledger` 永远是事实源
- 所有上下文读取先经过统一的 session 视图构建
- 所有上下文写入先回到 ledger，再形成 step 合同
- `context_reads/context_writes` 是正式合同，不是脆弱提示
- 生成态不猜测上下文，只消费合同并重建
- 缺失上下文时应显式暴露问题，而不是静默退回旧行为

### 新增组件：`SessionContextService`

建议新增一个 session 级上下文服务，用于统一管理录制态和生成态的上下文读写语义。

职责包括：

1. 从 `context_ledger` 构建当前完整 `context dict`
2. 统一处理 context 读取
3. 统一处理 context 写入与覆盖
4. 统一生成 step 级 `context_reads/context_writes`
5. 导出生成态可消费的上下文合同与 rebuild 计划
6. 提供“查询当前上下文”的显式能力

建议提供以下核心接口语义：

- `build_current_context(session) -> dict[str, str]`
- `read(key) -> str | None`
- `read_many(keys) -> dict[str, str]`
- `write(key, value, metadata) -> None`
- `capture_step_contract(step, reads, writes) -> StepContextContract`
- `answer_context_query(query) -> ContextAnswerResult`
- `export_contract() -> SessionContextContract`

这里的接口是设计语义，不要求在首轮中严格按这一命名实现，但责任边界必须成立。

---

## 上下文模型与数据流

### 数据角色

完整方案中有三层上下文数据：

1. `context_ledger`
   持久事实源，存储 observed、derived、rebuild_actions 等信息。

2. `current_context`
   由 `SessionContextService` 从 ledger 补全出的当前 session 视图。所有录制态读取都从这里拿值。

3. `step context contract`
   描述单一步骤读取了哪些 key、写入了哪些 key、哪些 key 在生成态必须被重建。

### 统一数据流

期望的数据流如下：

```text
提取 / 推导 / AI script 写入
  -> SessionContextService.write(...)
  -> context_ledger 更新
  -> current_context 更新
  -> step context_writes 生成

后续动作读取
  -> SessionContextService.build_current_context(...)
  -> SessionContextService.read/read_many(...)
  -> step context_reads 生成

导出技能
  -> SessionContextService.export_contract()
  -> generator 生成 rebuild_context()
  -> execute_skill() 使用同构 context dict
```

### 设计约束

在这个模型下：

- 录制态不允许再直接从不同模块各自拼接 context dict
- generator 不允许再把 step 中的偶然字符串当作 context 语义来源
- 如果一个 key 能在生成态被可靠重建，就必须能追溯到 ledger 或正式合同

---

## 读取规则统一

### 总体规则

所有上下文读取都必须通过 `SessionContextService` 完成。`context_reads` 变成读取后的审计记录，而不是读取前的前置条件。

### `ai_script`

`ai_script` 路径继续暴露 `context.get(...)` 和 `context["key"] = value` 的编程接口，但执行前的 `context` 由 service 统一构建。

也就是说：

- 保留此前通过命名空间注入 `context` 的使用体验
- 移除由 `assistant.py` 临时零散构建 dict 的职责
- 让录制态 `ai_script` 与生成态 `rebuild_context()` 共享同一套字段语义

### 结构化动作

结构化 `fill`、`press` 以及后续需要引用 context 的动作，不应再依赖“是否预先带上 `context_reads` 才能取到值”。

正确顺序应为：

1. service 根据动作语义、变量引用和上下文查询结果判定读了哪些 key
2. service 提供对应实际值
3. step 在持久化时写入正式 `context_reads`

这意味着 `context_reads` 是读取结果的记录，而不是读取能力本身的前提。

### `answer`

`answer` 必须被拆成两类语义：

- 页面回答：答案来自当前页面
- 上下文回答：答案来自当前 session context

当用户问题属于“当前上下文中有哪些内容”“购买人当前值是什么”“列出已提取参数”等上下文查询时，系统应直接走 `SessionContextService.answer_context_query(...)`，返回当前 context 中已有的 key 和 value，而不是尝试从页面上重新定位内容。

如果问题不属于上下文查询，再走现有页面回答逻辑。

### Prompt 中的上下文暴露

为了减少模型在决策阶段“知道 key 名但不知道 value”的现象，`_build_messages()` 应接入 service 导出的当前 context 摘要。

这里应采用受控暴露：

- 包含当前已知 key 与 value
- 控制长度，避免过多噪声
- 标记值的来源类别时可选，但不应影响主语义

这样模型在决定动作类型时能与运行时看到同一份上下文视图。

---

## 写入规则统一

### 总体规则

任何会在后续步骤中复用的数据，只能通过 `SessionContextService.write(...)` 进入系统。

### 页面提取写入

对于 `extract_text` 等提取动作：

- 执行完成后由 service 统一写入 ledger
- service 决定是否提升为 durable context
- service 返回正式 `context_writes`

assistant 不再在多个位置独立决定 observed/derived 写入逻辑。

### `ai_script` 写入

对于 AI 脚本中的：

```python
context["invoice_no"] = value
```

service 应比较执行前后的 context 差异，并统一记录：

- 新增 key
- 更新已有 key
- 对应来源 step
- 对应 source_kind

不应只处理“新增 key”，否则上下文覆盖和重算场景会丢失语义。

### 派生值写入

对由 AI 推导、规范化或组合而成的可复用字段，也必须通过 service 写入，并明确区分 observed 与 derived。

### 查询结果写入边界

普通 `answer` 默认不应自动写入 context。只有显式提取、用户要求保存、或后续 replay 必需时，才可以进入 durable context。

这样可以避免自然语言回答污染上下文字典。

### Step 合同生成

每次步骤执行前后，service 负责生成统一合同：

- 执行前记录当前上下文快照
- 执行中记录 reads
- 执行后比较 diff 得出 writes
- assistant 落库时直接使用 service 返回的合同结果

---

## 生成态收口

### 总体规则

生成态不再根据 step 文本或 fallback 占位符推测上下文，而是统一消费 `SessionContextService.export_contract()` 导出的合同视图。

### `rebuild_context()`

`rebuild_context()` 的职责应限制为：

1. 重放必要的前置页面动作以重新获取运行时必需值
2. 注入可直接复用的稳定值
3. 构建与录制态同构的最终 `context dict`

其输入应来源于：

- `context_ledger`
- 正式的 step context contract
- service 导出的 rebuild 计划

而不是 generator 再做一轮隐式推断。

### 生成代码契约

生成器对上下文的正常输出应统一成：

```python
context.get("buyer", kwargs.get("buyer", ""))
```

不应再把：

```python
kwargs.get("textbox", "context:buyer")
```

作为正常主路径输出。

### 旧 placeholder 的地位

旧 placeholder 逻辑只保留为历史数据兼容层，而不是新录制和新导出的主路径。

如果 generator 在新合同路径里发现某个 runtime-required key 没有来源，应显式报错或生成明确的失败信号，而不是悄悄回退到旧语义。

---

## 错误处理

### 缺少上下文

当动作读取某个 key 但当前 context 中不存在时，系统必须显式区分：

- key 从未写入
- key 理应存在但读取失败

不能静默回退到旧 placeholder 字符串，也不应默认吞掉错误继续执行。

### `answer` 查询失败

如果问题属于上下文查询但当前 context 中没有对应数据，应明确返回：

- 当前已知的 key 列表
- 哪些请求的 key 尚未记录

不能自动降级为页面抽取。

### 写入冲突

当新值覆盖旧值时，应记录 old/new/source 信息，便于后续回放差异排查。

### Rebuild 计划不完整

如果生成态发现某个 runtime-required key 无法从 ledger 或合同重建，应在导出或测试阶段显式失败。

---

## 兼容策略

### 读取兼容

历史 step 数据中可能存在旧格式：

- `value="context:buyer"`
- 缺失正式 `context_reads`

系统应在读取层尽量把这些旧数据解释为正式上下文引用，以保证已有录制仍可使用。

### 写入收敛

新录制一律写正式 `context_reads/context_writes` 合同，不再继续生成旧式 placeholder 语义。

### 导出兼容

generator 可以保留对旧数据的短期兜底，但默认策略必须是 contract-first，并以新合同语义输出新代码。

---

## 影响范围

### 核心文件

本设计预计涉及以下模块：

- `RpaClaw/backend/rpa/assistant.py`
- `RpaClaw/backend/rpa/context_ledger.py`
- `RpaClaw/backend/rpa/generator.py`
- `RpaClaw/backend/rpa/manager.py`
- `RpaClaw/backend/route/rpa.py`
- `RpaClaw/backend/tests/test_rpa_assistant.py`
- `RpaClaw/backend/tests/test_rpa_generator.py`

如有必要，可以新增一个上下文服务模块，例如：

- `RpaClaw/backend/rpa/session_context_service.py`

### 数据兼容影响

本轮设计不要求对历史数据做一次性迁移，但要求：

- 新录制数据采用新合同
- 历史数据可被读取兼容
- 导出时尽量规范化为新合同输出

---

## 测试策略

完整修复至少应覆盖以下测试组：

### 1. 录制态上下文闭环

验证流程：

- 前一步提取值进入 ledger
- service 构建当前 context
- `ai_script`、结构化 `fill`、`answer` 都能读到同一份值

### 2. `ai_script` 读写

验证：

- `context.get("buyer")` 能读到已提取值
- `context["invoice_no"] = ...` 能回写 ledger
- 覆盖已有 key 时能记录更新

### 3. 上下文查询回答

验证：

- “当前上下文中有哪些内容”
- “购买人当前值是什么”

这类问题应走 context 查询路径，而非页面 locator 路径。

### 4. Generator 合同一致性

验证新录制导出代码时：

- 使用 `context.get(...)`
- 不再生成旧式 `'context:buyer'` 主路径
- `rebuild_context()` 与 ledger/export_contract 一致

### 5. 历史数据兼容

验证旧格式 step 数据仍可运行，并在导出时尽量转成新合同语义。

### 6. 错误处理

验证以下场景的显式失败行为：

- 缺少必需 key
- rebuild plan 无法闭合
- 上下文查询 key 不存在

---

## 实施顺序建议

推荐的实施顺序如下：

1. 新增 `SessionContextService` 及其核心读写接口
2. 将 `assistant.py` 的 `ai_script`、结构化动作、`answer` 接入 service
3. 将 step 合同生成统一收口到 service
4. 将 generator 改为消费 service 导出的合同，降低旧 placeholder 的优先级
5. 补齐回归测试矩阵

这样的顺序能先统一语义层，再统一生成态，最后用测试锁住行为。

---

## 风险与缓解

### 风险 1：改动跨多个入口，容易出现局部接入

缓解：

- 将 service 定义为唯一上下文入口
- 在测试中覆盖 `ai_script`、结构化动作、`answer` 三条主路径

### 风险 2：兼容旧录制数据时再次传播旧语义

缓解：

- 明确“读兼容旧格式，写只写新合同”
- generator 对旧 placeholder 保留兼容，但不再作为新输出主路径

### 风险 3：上下文曝光过多导致 prompt 噪声

缓解：

- 在消息构建层输出摘要化 context snapshot
- 限制长度，优先保留近期或 runtime-required 字段

---

## 成功标准

本设计完成后，应达到以下结果：

- 录制态与生成态重新通过同一上下文命名空间连接
- `ai_script`、结构化动作、`answer` 对同一 context 视图行为一致
- 导出脚本不再依赖旧式 `context:...` placeholder 作为主路径
- 当上下文缺失或无法重建时，系统显式报错而不是静默退化
- 新增测试可以稳定阻止同类回归

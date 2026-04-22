# RPA Context Transfer And AUI Date Extraction Design

日期: 2026-04-22

## 概述

当前 RPA 技能录制在“先从详情页提取字段，再把字段填入表单”的场景中仍会出现两个回归：

1. 上下文值在生成代码时退化成录制样例值或匿名表单参数
2. 多字段填表步骤在记录或生成过程中丢失、覆盖，甚至出现目标字段和值串位

典型错误输出如下：

```python
context["buyer"] = '李雨晨'
context["expected_delivery_date"] = '2026-04-18'
await frame_scope.get_by_role("textbox", name="购买人", exact=True).fill(
    kwargs.get('textbox_5', '2026-04-18')
)
```

正确行为是：

```python
extract_text_value = await current_page.locator('[data-field="requestor"]').inner_text()
context["buyer"] = extract_text_value

await frame_scope.get_by_role("textbox", name="购买人", exact=True).fill(
    context.get("buyer", kwargs.get("buyer", ""))
)
```

本设计修复上下文传递合同，并补充 AUI display-only 日期字段提取能力。目标是让生成代码始终保留 `extract_text -> context_writes -> fill context_reads` 的语义链路，不再从硬编码样例值恢复上下文内容。

---

## 目标

- 让上下文传递型填表步骤稳定生成 `context.get(key, kwargs.get(key, ""))`
- 禁止上下文型 fill 退化为 `kwargs.get("textbox_5", "录制值")`
- 禁止 `extract_text` 产生的上下文在生成态被写死为 ledger observed value
- 保留多字段提取和多字段填表的所有步骤，避免去重覆盖和字段串位
- 让 `SessionContextService / context_ledger` 成为 step 读写合同的唯一语义来源
- 支持从 AUI 表单 DOM 中稳定提取 `期望完成时间 (UTC+08:00)` 与 `2025-06-13`
- 用回归测试覆盖 PR 字段搬运和 AUI 日期字段提取

---

## 非目标

- 不重写整个 RPA assistant 或生成器
- 不移除普通非上下文 fill 的参数化能力
- 不要求生成脚本在每个 context 读取处额外加入运行时强校验
- 不依赖 LLM 必须输出完美的 `${buyer}` 或 `context:buyer` 占位符
- 不为特定 PR/eBuy 页面写死业务字段规则

---

## 问题总结

### 上下文读写合同断裂

生成器已经支持：

```python
context.get("buyer", kwargs.get("buyer", ""))
```

但它只有在 fill step 明确带有 `context_reads=["buyer"]` 时才会走这条路径。

当前 structured fill 可能记录为：

```json
{
  "action": "fill",
  "value": "李雨晨",
  "target_hint": {"role": "textbox", "placeholder": "从详情页提取购买人"}
}
```

由于 `value` 是真实值，而不是上下文引用，step 没有稳定声明 `context_reads`。生成器因此把它当成普通输入值，最后退化为匿名控件参数：

```python
kwargs.get("textbox_5", "李雨晨")
```

### rebuild_context 错误使用样例值

`context_ledger` 里会保存 observed value，例如 `buyer = 李雨晨`。这份值是录制时的观测结果，不能作为页面提取型上下文的生成态来源。

如果存在原始 `extract_text` step，生成态必须重新执行 locator：

```python
extract_text_value = await locator.inner_text()
context["buyer"] = extract_text_value
```

不能直接生成：

```python
context["buyer"] = "李雨晨"
```

### 多字段步骤丢失和串位

用户一次要求填入多个字段时，assistant 会输出 JSON array。每个 action 都应成为独立 step。

错误表现是 5 个 fill 只剩 1 个，并且把日期值填入“购买人”：

```python
await frame_scope.get_by_role("textbox", name="购买人", exact=True).fill(
    kwargs.get("textbox_5", "2026-04-18")
)
```

这说明记录、持久化、去重或生成阶段没有保持 intent、target、value、context_reads 的一一对应。

### AUI display-only 字段值易被误读

AUI 表单中日期字段常以 display-only 结构呈现：

```html
<div class="aui-form-item" data-prop="expectedCompletionDate">
  <span class="label">期望完成时间 (UTC+08:00)</span>
  <span class="aui-input-display-only__content">2025-06-13</span>
  <input title="2025-06-13" class="aui-input__inner">
</div>
```

页面附近可能还有隐藏 date picker，包含 `2026 年`、`4 月` 等文本。提取逻辑必须限定在 `data-prop="expectedCompletionDate"` 对应的 field group 内，不能误读浮层文本。

---

## 方案选择

### 方案 1：只在 generator 匹配录制值

生成时发现 fill 值等于某个 context value，就替换为 `context.get(...)`。

优点：

- 改动小

缺点：

- 修复太晚，step 本身仍缺少 `context_reads`
- 配置页、测试页、导出元数据仍可能不一致
- 会鼓励 generator 继续根据样例值猜语义

### 方案 2：只加强 prompt

要求 LLM 输出：

```json
{"value": "${buyer}"}
```

优点：

- 语义直观

缺点：

- 依赖 LLM 稳定性
- AI 仍可能输出真实值
- 不能修复已有或历史路径中的真实值 step

### 方案 3：在 service 合同层绑定 context_reads

assistant 执行 structured fill 后，在持久化前通过 `SessionContextService` 判断填充值是否来自当前上下文。若匹配，则给 step 补充 `context_reads`。generator 只消费正式合同，不从硬编码样例值推导上下文。

优点：

- 不依赖 LLM 完美输出
- step、ledger、generator 合同一致
- 可以覆盖配置页、测试页、导出脚本
- 能从根上防止上下文型 fill 退化为匿名参数

缺点：

- 需要协同修改 assistant、context service、generator 和测试

### 结论

选择方案 3。

---

## 核心设计

### 1. 上下文写入：extract_text 必须保留 live rebuild 来源

当 `extract_text` step 写入上下文：

```json
{
  "action": "extract_text",
  "result_key": "buyer",
  "context_writes": ["buyer"],
  "target": "{\"method\":\"css\",\"value\":\"[data-field=\\\"requestor\\\"]\"}"
}
```

生成器必须在 `rebuild_context()` 中通过该 step 的 locator 重新提取：

```python
extract_text_value = await current_page.locator('[data-field="requestor"]').inner_text()
context["buyer"] = extract_text_value
```

如果 ledger 有 observed value，但也能找到对应 extract step，则 observed value 只能作为录制态事实和匹配依据，不能生成成上下文赋值常量。

只有在上下文值不是由页面 `extract_text` 产生，而是显式用户参数、派生值或非页面来源时，才允许导出为常量或 kwargs 入口。

### 2. 上下文读取：fill 必须声明 context_reads

structured fill 执行时，页面操作可以使用解析后的真实值：

```json
{"action": "fill", "value": "李雨晨"}
```

但持久化 step 前，assistant 必须通过 `SessionContextService` 的当前上下文视图匹配：

```text
buyer = 李雨晨
department = 研发效能组
inspector = 张雪
supplier = 联想华南直营服务中心
expected_delivery_date = 2026-04-18
```

若 fill value 与某个 context value 匹配，则 step 必须记录：

```json
{
  "action": "fill",
  "value": "李雨晨",
  "context_reads": ["buyer"]
}
```

匹配后，generator 生成：

```python
await locator.fill(context.get("buyer", kwargs.get("buyer", "")))
```

允许同名 kwargs fallback，因为它仍然保留上下文 key 的语义。禁止生成匿名控件参数：

```python
kwargs.get("textbox_5", "李雨晨")
```

### 3. context_reads 优先于普通参数化

fill 的生成顺序必须明确：

1. 如果 step 有 `context_reads`，生成 `context.get(key, kwargs.get(key, ""))`
2. 如果 step 有 legacy `context:key` 或 `${key}`，归一化为 `context_reads`，再走第 1 条
3. 只有普通非上下文 fill 才走现有参数化逻辑
4. 普通非参数文本仍可作为字符串常量

上下文型 fill 不能再使用 `_maybe_parameterize(value, params)` 产生控件名参数。

### 4. 多 action 持久化必须保持一一对应

对于用户指令：

```text
填入“购买人”，“使用部门”，“验收人”，“供应商”，“期望到货”
```

系统必须记录 5 个独立 fill step：

| 字段 | context_reads | 目标 |
| --- | --- | --- |
| 购买人 | `buyer` | 购买人输入框 |
| 使用部门 | `department` | 使用部门输入框 |
| 验收人 | `inspector` | 验收人输入框 |
| 供应商 | `supplier` | 供应商输入框 |
| 期望到货 | `expected_delivery_date` | 期望到货日期输入框 |

多 action 循环中，每个 intent 的 resolved locator、value、context_reads 必须立即绑定到当前 step，不能被后续 intent 覆盖。

AI fill step 默认不得被 `_deduplicate_steps()` 仅凭 `action + target` 覆盖。若确实需要 dedupe，至少要同时比较：

- `source`
- `frame_path`
- `target`
- `context_reads`
- `value`
- 字段 label/name/placeholder 诊断信息

### 5. AUI field_group 日期字段识别

snapshot/field_group 生成需要把以下 DOM 识别为一个字段组：

```html
<div class="aui-form-item" data-prop="expectedCompletionDate">
  <span class="label">期望完成时间 (UTC+08:00)</span>
  <span class="aui-input-display-only__content">2025-06-13</span>
  <input title="2025-06-13" class="aui-input__inner">
</div>
```

字段组语义应包含：

```json
{
  "field_name": "期望完成时间 (UTC+08:00)",
  "field_key": "expectedCompletionDate",
  "extraction_kind": "text",
  "locator": {
    "method": "css",
    "value": "[data-prop=\"expectedCompletionDate\"]"
  },
  "value_locator": {
    "method": "css",
    "value": "[data-prop=\"expectedCompletionDate\"] .aui-input-display-only__content"
  }
}
```

如果 `.aui-input-display-only__content` 不存在或为空，可退到：

```css
[data-prop="expectedCompletionDate"] input[title]
```

并读取 `title` 或 control value。

当用户要求“期望完成时间和具体的时间”时，可以拆成两个 extract_text：

```python
expected_completion_label = await current_page.locator(
    '[data-prop="expectedCompletionDate"] .field-header .label'
).inner_text()
context["expected_completion_label"] = expected_completion_label

expected_completion_date = await current_page.locator(
    '[data-prop="expectedCompletionDate"] .aui-input-display-only__content'
).inner_text()
context["expected_completion_date"] = expected_completion_date
```

提取值必须限定在 `expectedCompletionDate` 的 field group 内，不能匹配隐藏 date picker 中的年份、月份或日期按钮。

---

## 数据流

### 录制态提取

1. 用户要求提取多个字段
2. LLM 输出 JSON array，每个 action 带 `result_key`
3. runtime 解析 field group 或 locator
4. 执行 `extract_text`
5. assistant 通过 service 写入 ledger
6. step 持久化 `context_writes`

### 录制态填表

1. 用户要求把已提取字段填入当前表单
2. LLM 可输出真实值或 `${key}` 形式
3. runtime 执行 fill
4. assistant 通过 service 匹配当前 context value
5. step 持久化 `context_reads`
6. 多 action 每个 fill 独立 `add_step`

### 生成态导出

1. generator 从 service/exported contract 获取 rebuild sequence
2. `rebuild_context()` 对 extract_text writes 使用原始 locator 重新提取
3. normal path 中 fill reads 使用 `context.get(key, kwargs.get(key, ""))`
4. 普通非上下文 fill 继续使用现有 params 逻辑

---

## 错误处理

- 如果 fill value 匹配多个 context key，优先选择与 target label/name/placeholder 最相近的 key
- 如果无法消歧，保留普通 fill，不伪造 context_reads，并在 diagnostics 中记录 ambiguity
- 如果 step 已显式声明 `context_reads`，generator 必须按上下文读取生成，不能回退到样例值
- 如果 extract_text 的 rebuild 来源缺失，generator 不应生成 observed value 常量来伪装动态提取
- AUI field_group 解析到隐藏 date picker 文本时，测试必须失败

---

## 测试计划

### assistant 测试

- structured fill 的 value 等于 service 当前上下文值时，持久化 step 带 `context_reads`
- 多 structured fill 会持久化 5 个 step，不丢失、不覆盖、不串值
- `${key}` / `context:key` legacy 值会归一化为 `context_reads`
- value 匹配多个 key 时，不产生错误绑定，并记录诊断

### generator 测试

- 带 `context_reads=["buyer"]` 的 fill 生成：

```python
context.get("buyer", kwargs.get("buyer", ""))
```

- 上下文型 fill 不生成：

```python
kwargs.get("textbox_5", "李雨晨")
kwargs.get("textbox_5", "2026-04-18")
```

- extract_text `context_writes` 在 `rebuild_context()` 中通过 locator 重新提取
- ledger observed value 不覆盖 extract_text live rebuild
- PR 字段 fixture 生成 5 个 fill，每个 fill 读取对应 context key

### AUI 日期字段测试

- 给定最小 DOM fixture，snapshot 生成 `field_group`
- `target_hint: {"name": "期望完成时间"}` 解析到 `expectedCompletionDate`
- `extract_text` 读取 `2025-06-13`
- 不读取隐藏 date picker 中的 `2026 年`、`4 月`
- 用户要求字段名和值时，生成两个提取动作并保留两项结果

---

## 验收标准

用 PR/eBuy 示例重新录制并导出后，生成代码必须满足：

```python
extract_text_value_2 = await current_page.locator('[data-field="requestor"]').inner_text()
context["buyer"] = extract_text_value_2

await frame_scope.get_by_role("textbox", name="购买人", exact=True).fill(
    context.get("buyer", kwargs.get("buyer", ""))
)
```

并且包含 5 个独立 fill：

```python
context.get("buyer", kwargs.get("buyer", ""))
context.get("department", kwargs.get("department", ""))
context.get("inspector", kwargs.get("inspector", ""))
context.get("supplier", kwargs.get("supplier", ""))
context.get("expected_delivery_date", kwargs.get("expected_delivery_date", ""))
```

不得包含：

```python
context["buyer"] = "李雨晨"
context["expected_delivery_date"] = "2026-04-18"
kwargs.get("textbox_5", "2026-04-18")
```

AUI DOM fixture 中必须能提取：

```text
期望完成时间 (UTC+08:00)
2025-06-13
```

并且生成或解析路径必须使用 `data-prop="expectedCompletionDate"` 限定字段范围。

---

## 实施边界

首轮实现建议只改以下区域：

- `backend/rpa/assistant.py`
- `backend/rpa/session_context_service.py`
- `backend/rpa/generator.py`
- `backend/rpa/assistant_runtime.py` 或 snapshot field_group 相关 helper
- `backend/tests/test_rpa_assistant.py`
- `backend/tests/test_rpa_generator.py`
- `backend/tests/test_rpa_assistant_runtime.py`
- `backend/tests/test_rpa_snapshot_field_groups.py`

不需要修改前端交互，除非测试暴露配置页没有展示 `context_reads/context_writes` 的问题。


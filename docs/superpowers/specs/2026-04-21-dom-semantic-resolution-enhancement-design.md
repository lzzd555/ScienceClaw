# DOM 语义解析增强设计

日期: 2026-04-21

## 背景

当前项目的 RPA 录制系统使用 `SNAPSHOT_V2_JS` 在页面内遍历 DOM，通过启发式规则（前一个兄弟节点文本、`.field-label` class 名、`data-field` 属性）来关联字段名和字段值。这套机制在实际使用中遇到以下问题：

1. **语义关联不足**：字段名（如"联系人姓名"）和字段值（如"张三"）可能不在同一个 DOM 层级，中间隔着多层包装元素。当前启发式规则无法稳定关联。
2. **动态 ID 失效**：React/Vue 等框架生成的 CSS class 和 id 每次构建都不同，导致基于 CSS 的 locator 失效。
3. **框架特有模式未覆盖**：AUI 组件库使用 `data-prop` 属性而非标准 `id` 来关联 label-input，现有规则无法识别。

### 业界参考

研究了 Browser Use、Stagehand、Agent-E、Skyvern、LaVague 等项目后，归纳出三种主流方案：

- **Accessibility Tree 语义层**（Browser Use、Playwright MCP）：利用浏览器原生 AX API 解析 label-input 关联，覆盖标准 HTML。效率高，但无法处理框架自定义约定。
- **框架感知容器归并**（当前项目的 field_groups）：在 DOM 容器级别做 label-value 配对，可覆盖特定框架的模式。
- **LLM 语义兜底**（Stagehand、Agent-E）：将序列化 DOM 交给 LLM 做语义推断，覆盖规则无法处理的场景。

本设计采用**前两层**作为主要方案，AX Tree 优先、框架容器归并兜底，不引入 LLM 调用以避免额外延迟和成本。

## 目标

1. 对于标准 HTML 表单（`<label for="id">` + `<input id="id">`），利用浏览器原生 AX 信息自动解析 label-input 关联，无需手写规则。
2. 对于 AUI/Ant Design/Element UI 等框架的组件，扩展容器检测和字段关联规则，稳定关联字段名和字段值。
3. 保持现有 `field_groups` 数据结构和回退机制不变，仅增强解析能力。

## 设计

### 一、AX 语义优先级调整

**现状**：`fieldNameFromElement()` 已经有 `el.labels`、`aria-labelledby`、`aria-label` 的处理，但与启发式规则（前一个兄弟节点、`.field-label` class）混在一起，没有明确的优先级。

**改进**：明确优先级，浏览器原生 AX 信息优先于启发式规则。

优先级从高到低：

1. `el.labels`（标准 `<label for="id">` 关联）
2. `aria-labelledby`（ARIA 标准属性）
3. `aria-label`（直接内联名称）
4. **新增：框架容器内标签**（通过 `closest()` 找到字段容器，在容器内查找 label 元素）
5. `placeholder`
6. `title`
7. `getAccessibleName(el)`（Playwright recorder 的 AX 计算）
8. role 兜底

### 二、框架感知容器检测增强

**现状**：`ensureContainer()` 的 `closest()` 选择器只识别 `table, ul, ol, form, section, article`。

**改进**：新增字段容器模式：

```
.aui-form-item, .ant-form-item, .el-form-item,
[data-prop], .field-panel, .field-item,
.aui-collapse-item__content
```

这些容器是表单中 label + value 的自然分组边界。当 `aui-form-item` 被识别为容器后，其内部的 label 元素和 value 元素就可以通过容器 ID 关联起来。

### 三、框架特定关联策略

#### 策略 A：`data-prop` ↔ `label[for]` 匹配

适用于 AUI 组件。

模式：
```
div.aui-form-item[data-prop="expectedCompletionDate"]
├── label[for="expectedCompletionDate"] → 字段名
└── div.aui-form-item__content
    └── ... span.aui-input-display-only__content → 字段值
```

匹配逻辑：
1. 检测容器有 `data-prop` 属性
2. 在容器内找 `label[for="{data-prop}"]` 获取字段名
3. 在容器内找 `.aui-input-display-only__content` 或 `[data-field]` 获取字段值
4. 用 `[data-prop="{value}"]` 作为稳定的 value_locator

#### 策略 B：容器内 label + content 分区匹配

适用于 AUI、Ant Design、Element UI 等框架。

模式：
```
div.form-item
├── .form-item__label → "联系人姓名"
└── .form-item__content
    └── input/span → "张三"
```

匹配逻辑：
1. 在容器内查找 label 区域元素（`.aui-form-item__label`, `.ant-form-item-label`, `.el-form-item__label`, `label`）
2. 在容器内查找 value 区域元素（`.aui-input-display-only__content`, `.ant-form-text`, `.el-form-item__content input`, `[data-field]`）
3. 用 `data-prop` 或 `data-field` 作为稳定 locator（如果有），否则用 role+name

#### 策略 C：通用兄弟关联增强

当上述策略都不匹配时，回退到通用规则：

1. 找到字段的 `aui-form-item` 容器（或类似的 form-item 容器）
2. 在容器内的所有文本元素中，找到位置最近、在控件之前出现的文本作为字段名
3. 比较距离时给 Y 轴方向加权（同一行的权重高于跨行的）

### 四、field_groups 生成流程调整

在 `SNAPSHOT_V2_JS` 的 field_groups 生成阶段，增加框架感知归并：

```
1. 遍历 actionable_nodes 中的表单控件
2. 对每个控件，按优先级尝试获取字段名：
   a. el.labels / aria-labelledby / aria-label（AX 语义）
   b. 框架容器内标签（策略 A/B）
   c. getAccessibleName / placeholder / title（兜底）
3. 找到字段名后，在容器内查找对应的值节点
4. 生成 field_group，使用最稳定的 locator
```

### 五、回退机制

保持现有回退机制不变：

```
1. extract_text 优先匹配 field_groups
2. field_groups 命中但 value_locator 校验失败 → 回退到 content_nodes
3. content_nodes 也失败 → 返回提取失败
```

## 涉及文件

- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
  - `ensureContainer()`：扩展容器选择器
  - `fieldNameFromElement()`：调整优先级，增加框架容器查找
  - 新增 `matchByDataProp()`：策略 A 实现
  - 新增 `matchByFormContainer()`：策略 B 实现
  - field_groups 生成循环：使用新策略
- `RpaClaw/backend/rpa/assistant_runtime.py`
  - 无变更（执行流程不变，使用 field_groups 的方式不变）

## 测试要点

1. 标准 HTML 表单（`<label for>` + `<input id>`）
   - 期望：AX 语义自动关联，无需启发式规则

2. AUI 复杂表单（`data-prop` + 嵌套 display-only）
   - 期望：通过策略 A 正确关联"期望完成时间"和"2025-06-13"

3. Ant Design / Element UI 表单
   - 期望：通过策略 B 正确关联

4. 现有场景回归
   - 期望：所有现有 field_groups 功能不受影响

5. 回退测试
   - 期望：框架容器策略失败时，回退到 content_nodes

## 非目标

- 不引入 LLM 调用做语义解析
- 不修改 field_groups 的数据结构
- 不修改 extract_text 的执行流程
- 不修改上下文 ledger 的数据结构
- 不做全局 DOM 精简

## 风险与约束

- 新增的容器选择器如果过于宽泛，可能把非表单容器误识别为字段容器。需要在匹配后校验容器内确实有 label 和 value 元素。
- `data-prop` 是 AUI 特有的属性，其他框架可能不使用。策略 A 仅作为 AUI 的特定处理。
- 容器检测的 `closest()` 选择器列表需要随支持的框架逐步扩展，不能一次覆盖所有框架。

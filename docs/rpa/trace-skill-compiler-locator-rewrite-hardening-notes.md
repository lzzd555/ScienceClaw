# TraceSkillCompiler locator rewrite hardening notes

## Background bug

The Jalor export-table recording issue exposed two separate layers of failure.

The first failure was in recording-time page facts. Jalor grid renders the header
table and body table separately. Without a framework-level snapshot adapter, the
runtime agent generated a page-wide locator such as:

```python
page.locator("tbody tr").first.locator("td:nth-child(1) a")
```

That locator could resolve against the left navigation tree instead of the export
table, causing Playwright strict mode violations. This was addressed by adapting
`.jalor-igrid` into a structured `table_view`, with scoped row locators such as:

```python
page.locator("#taskExportGridTable tbody.igrid-data tr.grid-row").nth(0)
```

and row-relative cell actions such as:

```python
td[field="tmpName"] a
```

The second failure happened after recording, during script generation. The
recording-time AI operation produced correct scoped Jalor code:

```python
_rows = page.locator('#taskExportGridTable tbody.igrid-data tr.grid-row')
_row = _rows.nth(0)
await _row.locator('td[field="tmpName"] a').click()
```

But the generated script rewrote the collection locator into an unrelated
alternate locator:

```python
_rows = page.get_by_role('link', name='W3主页')
_row = _rows.nth(0)
await _row.locator('td[field="tmpName"] a').click()
```

The root cause was in `TraceSkillCompiler._rewrite_random_like_locator_in_code`.
The old guard protected direct chained collection usage:

```python
page.locator(selector).nth(0)
```

but did not recognize the two-step collection pattern:

```python
rows = page.locator(selector)
row = rows.nth(0)
```

As a result, a structural collection locator was treated as a replaceable
single-target locator and was rewritten to the only high-confidence alternate
candidate.

## Fix already applied

Two related commits currently exist on `codex/rpa-trace-first-recording`:

```text
f608a9d fix: support Jalor grid snapshot tables
9bdbba9 fix: preserve collection locators in AI trace compilation
```

The second commit only covers the observed pattern: a `page.locator(selector)`
assignment followed later by `.nth(...)` on the assigned variable.

## Generalized risk area

This should be treated as a broader class of bugs:

```text
Locator generalization rewrite can corrupt structural locator semantics.
```

The rewrite is valuable when replacing a genuinely unstable single action target,
for example:

```python
await page.locator('[data-testid="random-btn-a1b2"]').click()
```

But it is unsafe when the locator participates in collection, container, parent
scope, row scope, frame scope, or derived-locator semantics.

## Patterns to review

Future hardening should check at least these forms.

Collection assigned before `nth`:

```python
rows = page.locator(".row")
row = rows.nth(0)
```

Collection assigned before `first` or `last`:

```python
links = page.locator(".file-link")
await links.first.click()
await links.last.click()
```

Collection assigned before filtering:

```python
items = page.locator(".row")
target = items.filter(has_text="xxx").nth(0)
```

Parent or row-scoped locator:

```python
row = page.locator(".grid-row").nth(0)
await row.locator("td[field='tmpName'] a").click()
```

Container or region scope:

```python
panel = page.locator("#exportPanel")
await panel.get_by_text("导出全部列").click()
```

Multi-step derived locator chain:

```python
grid = page.locator("#taskExportGrid")
body = grid.locator("tbody.igrid-data")
row = body.locator("tr.grid-row").nth(0)
```

Frame-scoped locator:

```python
frame = page.frame_locator("#mainFrame")
rows = frame.locator(".grid-row")
```

## Suggested rule

Use a conservative boundary:

```text
Only rewrite an original locator when it clearly represents a single action target.
Do not rewrite a locator once it participates in collection, container, parent
scope, row scope, frame scope, filter, first, last, nth, or derived-locator logic.
```

This keeps locator repair useful for unstable single targets while protecting
semantic structure that the runtime AI intentionally built.

## Suggested regression matrix

Allow rewrite:

```python
await page.locator('[data-testid="random-btn-a1b2"]').click()
```

Forbid rewrite:

```python
await page.locator(selector).nth(0).click()
rows = page.locator(selector); await rows.nth(0).click()
container = page.locator(selector); await container.locator("child").click()
items = page.locator(selector); await items.filter(has_text="x").click()
links = page.locator(selector); await links.first.click()
frame = page.frame_locator("#f"); await frame.locator(selector).click()
```

## Recommended future task

Task name:

```text
Harden AI trace locator rewrite against structural locator misuse
```

Suggested scope:

```text
RpaClaw/backend/rpa/trace_skill_compiler.py
RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py
```

Avoid changing snapshot, manager, or recorder capture for this task unless new
evidence shows the bug originates outside the compiler rewrite boundary.

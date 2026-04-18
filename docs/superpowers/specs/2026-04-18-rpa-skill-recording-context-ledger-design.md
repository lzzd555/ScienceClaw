# RPA Skill Recording Context Ledger Design

## Goal

Redesign the skill-recording production flow around a task-scoped context ledger so that:

- recorder users no longer need to switch into a separate "agent mode"
- one AI recording task stays inside one continuous assistant context from start to finish
- exported skills can rebuild the same minimum required runtime context during replay
- user-requested extracted content is always preserved in context and can be consumed by later steps
- nonessential observations stay out of context so replay remains stable and understandable

## Scope

In scope:

- AI-first recording flow in `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- task-scoped assistant behavior in `RpaClaw/backend/rpa/assistant.py`
- recording-session state and persistence in `RpaClaw/backend/rpa/manager.py`
- script generation in `RpaClaw/backend/rpa/generator.py`
- exported skill metadata in `RpaClaw/backend/rpa/skill_exporter.py`
- replay contract for context reconstruction and cross-page data passing

Out of scope:

- a full replacement of the current recorder runtime architecture
- a new DSL for all control flow or workflow editing
- preserving every transient DOM observation in exported skills
- redesigning manual recording outside the minimum contract updates needed for replay correctness

## Problem Summary

The current recorder flow still has three product-level mismatches with the intended skill-making workflow.

First, the recorder UI exposes a user-visible toggle between ordinary assistant behavior and "agent mode". That makes the user choose execution strategy manually, even though the recording task should feel like one continuous activity.

Second, the current export path focuses on replaying recorded actions, but it does not formally preserve the minimum context that made those actions valid during recording. A new skill can therefore lose critical dependencies such as values extracted from page A and later filled into page B.

Third, there is no explicit contract for deciding which AI observations become durable replay context. Without that contract, the system either misses required data or risks over-storing noisy DOM observations that do not matter at replay time.

## User Requirements

This design is constrained by the following accepted requirements:

- The chosen scope is AI recording first, with permission to adjust small parts of manual recording and export contracts only when needed to guarantee replayability.
- Replayability is the top priority.
- "Replayable" means the skill can run in a fresh browser context and rebuild the minimum context required for later steps to succeed.
- If the user explicitly asks to extract some content, that extracted content must be stored in context.
- Other observed values should only enter context when they are necessary at runtime to rebuild the replay path.
- The recording task itself must stay in one assistant context for the whole task.
- The exported script does not need to replay the original assistant conversation, but it must reconstruct the same effective runtime context through code.

## Alternatives Considered

### 1. Minimal Patch Design

Remove the visible mode toggle and attach a few more metadata fields to existing AI steps.

Pros:

- smallest implementation footprint
- lower short-term change risk

Cons:

- context remains scattered across step metadata
- replay still depends on implicit assumptions instead of a formal rebuild contract
- does not reliably solve cross-page extracted-value replay

### 2. Task Context Ledger Design

Model one AI recording task as one assistant context plus one formal context ledger. Record not only actions, but also the minimum context values and rebuild steps needed for replay.

Pros:

- directly matches the accepted product requirements
- removes the visible mode switch cleanly
- gives export and replay a formal source of truth
- keeps context minimal and intentional

Cons:

- requires coordinated changes across assistant, session state, generator, and exporter

### 3. Full DOM Snapshot Preservation

Store large DOM contexts or snapshots for each AI turn and try to recreate replay conditions from those records.

Pros:

- preserves maximal information

Cons:

- large and noisy persistence model
- poor signal-to-noise ratio for replay generation
- does not actually guarantee deterministic reconstruction of historical DOM states

## Chosen Approach

Choose the task context ledger design.

This design keeps the current recorder architecture, but adds one formal task-level context object that records only the values and rebuild steps that matter for replay. It removes the visible agent-mode toggle, keeps the recording task inside one assistant context, and upgrades export from "action playback" into "context rebuild plus task execution".

## Architecture

### Core Model

One AI recording task is represented by three linked layers:

1. `RPA session`
   Owns browser, tab, frame, lifecycle, and recorded steps.

2. `task context`
   Represents the one continuous assistant context for this recording task.

3. `context ledger`
   Records the minimum replay-relevant context facts that later steps depend on.

The session tracks browser truth. The steps track action truth. The ledger tracks context truth.

### Context Ledger Shape

The ledger should contain four categories of data:

- `page_context`
  Minimum page-entry conditions required to rebuild context, such as start URL, required tab, or frame scope.

- `observed_values`
  Values extracted from DOM that the user explicitly requested or that later steps consume.

- `derived_values`
  Temporary AI-derived values promoted into durable context only if replay needs them.

- `rebuild_actions`
  Declarative or step-like records describing how replay should reconstruct the context values in a fresh browser session.

The critical rule is that a stored value is not enough by itself. Replay also needs to know how to reacquire that value.

### Step-to-Context Relationship

Recorded steps should keep their action semantics, but also declare context interaction explicitly:

- ordinary action step
  may read context values

- extraction step
  writes context values

- `ai_script` step
  may read and write context values and may encapsulate part of context rebuilding when genuinely necessary

This allows export to derive a stable execution order:

1. rebuild required context
2. make context values available
3. execute business steps that consume them

## Recording Flow Design

### Unified Entry

The recorder UI should expose one AI recording entry only. Users should not choose between `chat`, `react`, or a visible "agent mode".

Internal execution strategy still exists, but becomes a backend decision:

- answer only, no step persistence
- structured atomic action
- extraction action with context writes
- control-flow escalation into `ai_script`

The recorder should present these as one continuous task rather than multiple modes.

### One Task, One Assistant Context

The recording task owns one `task_context_id` from the first task-setting instruction until the user finishes the recording task. Each AI turn reads from and writes back to that same task context.

The task context should retain:

- task goal
- accumulated recorded steps
- latest page observation summary
- promoted context variables
- variable dependencies
- rebuild plan entries

This replaces the current user-visible mode switch with a task-scoped state model.

### Per-Turn Flow

Each AI recording turn should follow this sequence:

1. load current task context
2. observe active page and tab
3. classify current user instruction into answer-only, atomic action, extraction, or complex control flow
4. execute or generate the step
5. determine whether the turn produced context-worthy values
6. promote eligible values into the context ledger
7. persist the step together with its context reads and writes

The important product behavior is that the assistant does not just "see" DOM context. It formally captures the subset needed for replay.

## Context Promotion Rules

The context-promotion rules are intentionally strict.

### Must Write To Context

The system must write a value into context when:

- the user explicitly asked to extract that content
- a later step consumes that value
- an `ai_script` step depends on that value as an input or produces it as an output
- the value is required for cross-page data transfer, such as reading a profile on page A and filling a form on page B

### May Write To Context

The system may write a value into context only when replay cannot be made reliable without it.

### Must Not Write To Context

The system must not write a value into context when:

- it was only part of transient page understanding
- it was never consumed later
- it is merely a candidate or explanatory observation
- replay does not need it as a runtime dependency

This keeps the ledger small, auditable, and replay-focused.

## Export And Replay Design

### Export Goal

Exported skills should no longer be modeled as plain action playback. They should be modeled as:

1. `context rebuild phase`
2. `task execution phase`

The exported script does not need to replay assistant reasoning. It needs to rebuild the runtime facts that later actions depend on.

### Replay Contract

Replay should rebuild context in a fresh browser environment by:

1. restoring the minimum navigation and page-entry conditions
2. rerunning required extraction actions
3. writing resulting values into runtime context
4. executing the downstream actions that consume those values

For example:

- open page A
- locate and extract requested person fields
- store them in runtime context
- navigate to page B
- read those values from context and fill the destination form

### Export Metadata Contract

The exported representation should explicitly include:

- `required_context_outputs`
- `context_rebuild_plan`
- `step_context_reads`
- `step_context_writes`

This metadata should become formal generator input instead of being buried in diagnostics.

### Generated Script Structure

The generator should emit a structure similar to:

```python
async def rebuild_context(page, context, **kwargs):
    # navigate to prerequisite pages
    # extract user-requested values
    # extract minimal runtime dependencies
    return context

async def execute_skill(page, **kwargs):
    context = {}
    context = await rebuild_context(page, context, **kwargs)
    # execute main task steps
```

If a complex task requires `ai_script`, that script should still participate in the same read/write context contract rather than bypassing the rebuild phase.

## Implications For Existing Modules

### `frontend/src/pages/rpa/RecorderPage.vue`

- remove the visible `agentMode` toggle and related copy
- present one continuous AI-assist flow
- show lightweight context events, such as when requested values were captured into context

### `backend/rpa/assistant.py`

- treat execution strategy as an internal planner concern
- identify user-explicit extraction targets
- decide which observed values must be promoted into context
- persist per-step context reads and writes

### `backend/rpa/manager.py`

- extend task/session state with a task-scoped context ledger
- persist context rebuild actions and variable dependencies next to step history

### `backend/rpa/generator.py`

- generate `rebuild_context(...)` before business-step execution
- consume formal context contracts instead of inferring replay dependencies from step text alone

### `backend/rpa/skill_exporter.py`

- export the context contract alongside `skill.py`
- describe context variables and replay behavior in skill metadata so downstream consumers understand replay assumptions

## Error Handling

The context ledger introduces several new failure classes that should be surfaced clearly:

- requested extraction target not found during recording
- required replay context cannot be rebuilt during export testing
- downstream step references a missing context variable
- context value produced in recording has no valid rebuild action

The system should fail early when a required context value cannot be reconstructed, because replayability is the top acceptance criterion.

## Testing Strategy

### Backend Tests

Add or expand tests to cover:

- user-requested extracted values are always persisted into context
- nonessential observations are not promoted
- steps record context reads and writes correctly
- generator emits context rebuild before dependent actions
- page-A-to-page-B data transfer works in replay

### Frontend Verification

Verify that:

- the visible agent-mode toggle is removed
- recorder UX still supports one continuous AI recording task
- context capture signals are understandable but not noisy

### Replay Acceptance Cases

The design is not complete unless these scenarios pass:

- extract a requested value from one page and fill it on another page
- replay the skill in a fresh browser context without relying on recording-time assistant memory
- keep unrelated DOM observations out of exported context
- execute a control-flow-heavy step through `ai_script` without breaking context reconstruction

## Risks

- If context promotion is too broad, exported skills will become noisy and hard to reason about.
- If context promotion is too narrow, replay will fail because required values were not rebuilt.
- Existing historical recordings may not contain enough context metadata, so backward compatibility should degrade gracefully.
- `ai_script` integration can become an escape hatch unless its context reads and writes are formalized.

## Recommendation

Implement the task context ledger design as the new skill-recording production plan.

It best satisfies the accepted product direction:

- no user-visible switch into agent mode
- one continuous assistant context for one recording task
- explicit, minimal, replay-focused context persistence
- guaranteed preservation of user-requested extracted values
- exported skills that reconstruct the same effective runtime context through code

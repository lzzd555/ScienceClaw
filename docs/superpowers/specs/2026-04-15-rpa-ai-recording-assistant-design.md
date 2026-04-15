# RPA AI Recording Assistant Reliability And Control-Flow Design

## Goal

Improve the RPA AI recording assistant so that:
- Agent mode does not terminate immediately when a structured action fails
- Agent mode can self-correct through recovery loops before aborting
- Complex user instructions with conditions or loops can be supported
- Conditional or polling-heavy tasks can be preserved as a single exported `ai_script` step

## Scope

In scope:
- `react` mode planning and execution flow in `RpaClaw/backend/rpa/assistant.py`
- Structured action execution and error normalization in `RpaClaw/backend/rpa/assistant_runtime.py`
- Recorder-side agent progress and recovery feedback in `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- `ai_script` persistence and export metadata in `RpaClaw/backend/rpa/manager.py` and `RpaClaw/backend/rpa/generator.py`
- Tests covering structured-action recovery, escalation, and export behavior

Out of scope:
- Designing a full control-flow DSL or editable workflow IR
- Splitting an upgraded `ai_script` back into multiple editable standard steps
- Large recorder UI redesign beyond status and progress visibility
- Changes to manual recording semantics outside the AI assistant flow

## Problem Summary

The current assistant has two practical failures.

First, `react` mode does not treat structured-action execution failures as recoverable observations. Code execution already returns a normalized failure payload through `_execute_on_page()`, but structured actions go through `resolve_structured_intent()` and `execute_structured_intent()` without a local recovery wrapper. As a result, locator failures, frame mismatches, stale elements, and timeouts can bubble out of the agent loop, hit the SSE route error path, and terminate the task before the model can adjust its strategy.

Second, the current prompt and action model are biased toward single atomic browser operations. That works for instructions like "click the first result" but breaks down for tasks like "if the first result is not complete, wait 500ms and refresh until it becomes complete, then download it." Those tasks require branching, waiting, and iteration. They must be supported, but the accepted constraint is to preserve them as a single executable `ai_script` step rather than invent a new editable control-flow format.

## User Experience Requirements

The assistant should follow these UX rules:
- Prefer atomic structured steps when the task can be decomposed into ordinary actions
- Do not end the task on the first structured-action failure
- Show the user when the agent is recovering, retrying, or escalating
- Upgrade to a single `ai_script` step only when the task clearly requires control flow
- Keep the final recording exportable without special post-processing

The accepted behavior for complex tasks is:
- Try to solve the goal as ordinary structured substeps first
- If the task requires conditionals, polling, or state-dependent branching, upgrade that subtask to one `ai_script`
- Persist and export that upgraded result as one script step

## Recommended Approach

Use a layered recovery-and-escalation design rather than a prompt-only fix.

This design keeps the existing structured-action path as the default because ordinary click/fill/extract operations should remain explicit and lightweight. It then adds a recovery loop around execution so failures become inputs to the next planning turn instead of terminal route errors. When the user intent or repeated failures indicate true control flow, the agent escalates that subtask to `ai_script` and stores it as a single code step.

This approach is preferred over a minimal try/except patch because it addresses both reliability and capability. It is also preferred over a new control-flow DSL because the current product requirement explicitly accepts a single script step for complex conditions and loops.

## Architecture Changes

### Agent Loop Refactor

`RpaClaw/backend/rpa/assistant.py` should refactor the `RPAReActAgent` loop into three conceptual stages:

1. `plan_next_step`
- Input: user goal, existing recorded steps, latest page snapshot, recent recovery context
- Output: `structured_action`, `code_action`, `done`, or `abort`
- Default behavior: prefer `structured_action`

2. `execute_step`
- `structured_action` executes through `resolve_structured_intent()` and `execute_structured_intent()`
- `code_action` executes through `_execute_on_page()`
- Both paths must return the same normalized result envelope

3. `recover_or_escalate`
- On recoverable failure, capture the failure classification, re-observe the page, and feed the failure back into the model
- On repeated failure or control-flow detection, escalate to `code_action`
- Only abort on explicit fatal conditions or configured retry exhaustion

The key rule is that structured-action exceptions are no longer allowed to escape the agent loop as ordinary control flow.

### Unified Execution Result Envelope

Both structured and code execution paths should produce a common result shape:
- `success`
- `error_code`
- `error_message`
- `retryable`
- `observed_output`
- `step_payload`

This envelope becomes the only execution contract consumed by the agent loop. It removes the current asymmetry where code execution can be recovered but structured execution can crash the whole task.

## Prompt And Escalation Design

### Planning Contract

`REACT_SYSTEM_PROMPT` in `RpaClaw/backend/rpa/assistant.py` should explicitly require the model to choose an execution mode before returning the next step.

Recommended response shape:

```json
{
  "thought": "brief reasoning",
  "action": "execute|done|abort",
  "execution_mode": "structured|code",
  "operation": "navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "upgrade_reason": "conditional_branch|polling_loop|state_wait|dynamic_iteration|none",
  "target_hint": {},
  "collection_hint": {},
  "ordinal": "first",
  "value": "",
  "code": "async def run(page): ..."
}
```

`execution_mode=structured` means the model is committing to one atomic browser action.

`execution_mode=code` means the model is intentionally upgrading the current subtask into a single `ai_script` step.

### Upgrade Rules

The prompt should explicitly require escalation to `code` when the user instruction or recovery context includes:
- Conditional logic such as `if`, `else`, `unless`, or explicit state-branching language
- Polling or loops such as `until`, `repeat`, `every 500ms`, `wait until`, `retry until`
- State-dependent branching such as "if the first result is not complete, refresh"
- Decision-making based on extracted page state
- Composite logic that combines read, branch, wait, retry, and final action in one unit

The prompt should explicitly keep these cases in `structured` mode:
- Click the first or nth result
- Fill a field and submit
- Extract visible text
- Open a detail page and download when no conditional loop is involved

### Additional Prompt Constraints

The prompt should also enforce:
- No premature `done`
- First recovery attempt should correct locator, frame, or timing issues before escalating
- `code` output must always be a complete `async def run(page): ...`
- Code mode should solve only the current complex subtask, not expand into an unbounded end-to-end workflow

`SYSTEM_PROMPT` for non-agent assistant chat should also gain a weaker version of this rule so chat mode can directly emit Python for explicit condition/loop instructions instead of forcing them into a single structured action.

## Recovery And Error Handling

### Structured Action Error Normalization

`RpaClaw/backend/rpa/assistant_runtime.py` should classify structured-action failures into stable error codes instead of letting them propagate raw:
- `target_not_found`
- `multiple_targets_matched`
- `frame_not_found`
- `element_not_interactable`
- `navigation_timeout`
- `execution_timeout`
- `page_changed`
- `unexpected_runtime_error`

The runtime should capture enough detail for recovery, but the agent loop should consume these as classified failures rather than exception strings alone.

### Recovery Policy

`RpaClaw/backend/rpa/assistant.py` should apply recovery by failure type:

Locator failures:
- Rebuild the snapshot immediately
- Feed the failure plus current observation back to the model
- Retry with corrected locator, frame, or ordinal

Timing or state failures:
- Ask the model to consider wait, refresh, or re-observation
- If the same class of failure repeats and the goal contains control-flow semantics, escalate to `ai_script`

Fatal failures:
- Missing active page
- User abort
- Repeated unparsable model outputs
- Retry budget exhausted

Only fatal failures should emit final `agent_aborted` behavior.

### Retry Limits

To prevent loops, the agent should track:
- `same_step_retry_count`
- `consecutive_parse_or_runtime_failures`

Recommended defaults:
- Maximum 2 recovery attempts for the same step
- Maximum 4 consecutive parse or runtime failures for the task

When these limits are reached, the agent should stop with an explicit reason rather than a generic route error.

## Recorder UI Changes

`RpaClaw/frontend/src/pages/rpa/RecorderPage.vue` should expose recovery and escalation as first-class progress states instead of treating every failure as terminal.

Add these agent events:
- `agent_recovering`
- `agent_escalated`
- `agent_warning`

Recommended meanings:
- `agent_recovering`: the previous structured action failed, and the agent is re-observing or adjusting strategy
- `agent_escalated`: the agent detected control flow and upgraded the current subtask to script mode
- `agent_warning`: retry budget is close to exhaustion

The frontend should only mark the assistant response as failed on:
- Final `agent_aborted`
- Explicit fatal `error`

Recoverable failures should remain visible in the same conversation item as intermediate state rather than ending the message immediately.

## Persistence And Export

### Step Persistence

When the agent upgrades a subtask to code, the recorded step should remain a first-class `ai_script` record rather than being decomposed later.

Required stored fields:
- `action = "ai_script"`
- `source = "ai"`
- `prompt = original complex instruction`
- `description = user-readable summary`
- `value = full normalized function text in the form async def run(page): ...`
- `assistant_diagnostics.execution_mode = "code"`
- `assistant_diagnostics.upgrade_reason`
- `assistant_diagnostics.recovery_attempts`

This makes upgraded script steps inspectable and distinguishable from ordinary AI actions.

### Export Behavior

`RpaClaw/backend/rpa/generator.py` already embeds `ai_script` steps. Keep that model and strengthen the contract:
- Agent code must be normalized to `async def run(page): ...`
- Storage format should always be the full normalized function so the generator never has to guess whether it is wrapping a function or raw body
- Exported code should add a short comment noting that the step was upgraded from the AI assistant due to control-flow requirements

The export system should not attempt to reconstruct ordinary click or fill steps from an upgraded script step.

## Testing

### Runtime Tests

Add tests for `RpaClaw/backend/rpa/assistant_runtime.py` covering:
- structured-action exceptions are normalized instead of escaping
- expected error codes are emitted for missing target, multi-match, frame mismatch, and timeout paths

### Agent Tests

Add tests for `RpaClaw/backend/rpa/assistant.py` covering:
- ordinary atomic instructions remain `structured`
- conditional or loop instructions become `code`
- structured failure triggers recovery instead of immediate abort
- repeated failures can trigger `agent_escalated`

### End-To-End Smoke Cases

Cover at least these scenarios:
- "click the first result and download it"
  Expected: stays in structured mode
- "if the first result is not complete, wait 500ms and refresh until complete, then download it"
  Expected: attempts ordinary planning first, then upgrades the complex subtask to one `ai_script`
- short-lived element interaction failure
  Expected: recovery path succeeds without aborting
- page change or disappearing target
  Expected: at least one recovery cycle before final escalation or abort

## Acceptance Criteria

The design is complete when these outcomes are true:
- Structured-action execution failures in agent mode no longer terminate the task immediately through the route error path
- The assistant can recover from locator, frame, and timing failures when a correction is plausible
- Explicit conditional or polling-style instructions can be completed by upgrading to one `ai_script` step
- Upgraded script steps are stored, displayed, and exported as one step
- Recorder UI makes recovery and escalation visible to the user
- Regression coverage exists for recovery, escalation, and export behavior

## Risks

Primary risks:
- Over-escalating ordinary tasks into code mode
- Under-escalating control-flow tasks and getting stuck in retry loops
- Letting recovery loops become noisy or confusing in the recorder UI
- Inconsistent stored format for `ai_script` causing export fragility

Mitigations:
- Explicit `execution_mode` and `upgrade_reason` fields
- Stable retry budgets
- Dedicated recovery and escalation UI events
- One normalized script storage contract

## Implementation Notes

This work should be implemented as an assistant reliability improvement, not a full workflow-language project.

The minimum acceptable outcome is:
- structured-action failures are recoverable inside the agent loop
- prompts clearly distinguish structured mode from code mode
- control-flow tasks upgrade to one stored `ai_script`
- frontend communicates recovery and escalation
- export remains stable for upgraded script steps

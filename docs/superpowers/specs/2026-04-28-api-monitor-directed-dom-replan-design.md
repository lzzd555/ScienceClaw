# API Monitor Directed DOM Replan Design

## Context

API Monitor directed analysis currently builds one compact DOM snapshot, asks the LLM for a complete multi-action plan, then executes that plan sequentially. This creates a hard ceiling: after the first action changes the DOM, all later actions are still based on the old page state. The result is brittle one-step behavior on search flows, detail pages, modals, tabs, and any UI that reveals the next control only after interaction.

The goal is to raise directed analysis from one-shot planning to dynamic browser analysis while preserving the existing RPA architecture rules:

- Recording and analysis remain trace-first and observable.
- Failure facts, current URL/title, DOM state, and captured API calls are primary inputs.
- Safety filtering remains layered: system risks are blocked before execution; selector and page-state instability are handled after execution through observation and replanning.
- Experience hints and heuristics must not dominate the main path.

## Recommended Approach

Replace one-shot multi-action execution with a bounded `Sense -> Plan -> Act -> Observe -> Replan` loop.

Each loop iteration builds a fresh page snapshot, compacts it with the original user instruction plus run history, asks the LLM for exactly one next action or a `done` decision, executes that action if allowed, waits for DOM/network settling, drains newly captured API calls, and feeds those facts into the next iteration.

This keeps every next action grounded in the current DOM instead of stale assumptions from the initial page.

## Alternatives Considered

### Refresh Snapshot But Keep Original Plan

This is low cost but does not solve the core issue. The system would observe DOM changes, yet still execute actions chosen from the first snapshot. It improves logging without enabling adaptive next-step behavior.

### Full Browser Agent State Machine

This would provide broader autonomy, but it risks duplicating or swallowing responsibilities already covered by the RPA runtime agent. API Monitor should remain focused on targeted API capture and MCP tool generation, not become a general-purpose browser agent.

### Dynamic Single-Step Replanning

This is the chosen design. It gives the directed analyzer enough agency to follow DOM changes while keeping scope bounded, testable, and aligned with existing API Monitor responsibilities.

## Architecture

### Directed Step Decision

Add a single-step decision model alongside the existing action model:

- `goal_status`: `continue`, `done`, or `blocked`
- `next_action`: one `DirectedAction` when `goal_status` is `continue`
- `summary`: short explanation of the current decision
- `expected_change`: what should change after executing the action
- `done_reason`: why the goal is complete or blocked

The existing `DirectedAction` fields can be reused for Playwright operation details and business risk classification.

### Directed Run State

Maintain in-memory run state inside `analyze_directed_page`:

- original instruction
- mode and business safety level
- current step index
- executed actions
- skipped actions
- action failures
- new captured API calls
- current page URL and title
- latest DOM digest
- last observation summary

This state is prompt context for the next LLM call. It is not a heavy contract layer and should not replace real browser observation.

### Observation

Before each planning step, collect:

- current URL
- current title
- raw page snapshot via `build_page_snapshot`
- compact snapshot via `compact_recording_snapshot`
- DOM digest derived from visible interactive regions and page identity
- recent captured API count
- previous action result or error

After each action, wait for a short settle window:

- try `domcontentloaded` when navigation is likely
- wait briefly for network quietness where Playwright supports it
- poll the DOM digest until it stabilizes or times out

The settle logic should be best-effort and bounded. It should never become an infinite wait.

## Planner Prompt

Change the directed prompt from "generate the shortest executable operation plan" to "choose exactly one next operation based on the current page state."

Prompt rules:

- Return JSON only.
- Use the current compact snapshot as the source of truth.
- Historical actions explain what already happened; they are not pending instructions.
- If the page changed, reason from the new DOM.
- Return `done` once the target API appears captured or the instruction is satisfied.
- Return `blocked` when no safe or meaningful next browser action exists.
- Do not return Python, shell, file, permission, or local system operations.
- Preserve business risk classification for the proposed action.

## Execution Flow

`ApiMonitorSessionManager.analyze_directed_page` should become:

1. Drain historical calls into session history.
2. Emit `analysis_started`.
3. For each step up to `max_steps`:
   - emit snapshot progress
   - build current observation
   - ask `build_directed_step_decision`
   - emit `directed_step_planned`
   - stop on `done` or `blocked`
   - apply business safety filtering
   - emit `directed_action_skipped` when blocked by safety
   - execute one allowed action
   - mark action time for request evidence
   - emit `directed_step_executed`
   - observe DOM/network result
   - drain captured calls
   - emit `directed_step_observed`
4. Generate tool definitions from calls captured during this directed run.
5. Emit `analysis_complete`.

The existing `directed_plan_ready`, `directed_action_detail`, and `directed_action_executed` events can remain for compatibility during the transition, but new step-level events should become the preferred interface.

## Safety

Keep the current two directed modes:

- `safe_directed`: execute only actions with `risk=safe`; skipped unsafe actions are added to run history so the next step can attempt a safe alternative or stop as blocked.
- `directed`: allow business-unsafe actions when the user explicitly selected this mode.

System safety remains stricter than business safety. The planner cannot request shell commands, file operations, local permissions, downloads, or host access. These are rejected regardless of mode.

Selector fragility, missing elements, empty extraction, slow navigation, and changed page structure are not pre-blocked. They are execution facts that feed the next observation and replan.

## Failure Handling

When an action fails:

- capture the exact exception message
- keep current URL/title
- rebuild snapshot if possible
- drain any API calls that still happened
- add the failure to run history
- ask the planner for a repair step unless max failure count is reached

Stop as `blocked` when:

- repeated actions fail without DOM/API progress
- no meaningful visible candidate exists
- max steps or max duration is reached
- business safety blocks the only required action in `safe_directed`

## API Capture And Tool Generation

Only calls captured during the directed run should be passed to `_generate_tools_from_calls` for the run result. Pre-existing page-load calls still remain in session history for token-flow analysis.

The directed run should stop early when newly captured calls match the apparent instruction intent strongly enough, but the first implementation can use a conservative stop condition: stop only when the LLM returns `done` or the step budget is reached.

## Frontend/SSE Compatibility

Existing frontend behavior can continue consuming current events. Add new optional events for better visibility:

- `directed_step_snapshot`
- `directed_step_planned`
- `directed_step_executed`
- `directed_step_observed`
- `directed_replan`
- `directed_done`

No frontend redesign is required for the first implementation. The UI can render these as progress log entries.

## Testing

Add backend tests for:

- directed analysis replans from a second snapshot after the first action changes the DOM
- stale second actions from an initial plan are not blindly executed
- `safe_directed` filters unsafe actions on every step
- action failure is included in the next planning context
- captured calls are drained after each step and used for tool generation
- max step budget stops runaway loops
- route dispatch remains compatible for `safe_directed` and `directed`

Existing unit tests for locator building, action execution, and business safety filtering should continue to pass.

## Scope

In scope:

- backend planner model changes
- backend directed execution loop
- snapshot/observation helpers
- SSE event additions
- focused tests

Out of scope:

- a general-purpose browser agent
- new frontend workflow design
- persistent run-history storage
- heavy DOM contract generation
- rule-library or site-template driven planning

## Implementation Notes

Prefer adding new functions before removing old ones, so current tests and callers can migrate incrementally:

- `build_directed_step_decision`
- `execute_directed_step`
- `observe_directed_page`
- `run_directed_analysis_loop`

Keep `build_directed_plan` and `execute_directed_plan` initially for compatibility and targeted tests. Once the dynamic loop is stable, old multi-action plan execution can be deprecated.

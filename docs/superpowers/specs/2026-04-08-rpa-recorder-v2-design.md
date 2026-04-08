# RPA Recorder V2 Final Design

## Summary

The current RPA recorder is built around a Python-side Playwright session plus a page-injected custom recorder script. That design has two structural limitations:

- Locator generation is not based on the same semantics used later for replay, so recorded selectors often drift from what Playwright will actually resolve and act upon.
- Frame context is not modeled as first-class state, so interactions inside `iframe` elements are either missed entirely or cannot be replayed deterministically.

This design replaces the current recorder core with a Playwright-recorder-based engine that uses Playwright's own action, selector, frame, and signal semantics as the source of truth. The existing Python backend remains as the product-facing orchestration layer, persistence layer, API layer, skill export layer, and frontend gateway.

The result is a two-layer architecture:

- A new Node-based `rpa-engine` owns the real browser runtime, recording pipeline, selector generation, frame-aware action modeling, validation, and playback.
- The existing Python backend owns user sessions, persistence, product APIs, AI orchestration, and skill export.

This is not a minimal migration. It is the target architecture intended to fully solve locator quality, nested frame support, popup/new-tab behavior, and long-term recorder correctness.

## Goals

- Make recording, validation, playback, and code generation use the same Playwright semantics.
- Support same-origin and cross-origin `iframe` interactions as first-class recorded actions.
- Support popup/new-tab, navigation, download, and dialog behavior through explicit runtime signals.
- Replace single-locator recording with validated locator candidates and an explicit primary locator selection.
- Keep the existing product experience: FastAPI APIs, Vue pages, skills export, task orchestration, and local-first deployment.
- Allow old recorder codepaths to be removed completely. Existing recordings may be discarded.

## Non-Goals

- Preserving compatibility with previous recorder step formats.
- Keeping the current Python recorder runtime as a fallback implementation.
- Reproducing Playwright Inspector UI exactly.
- Supporting multiple recorder engines in parallel long term.

## Why Full Replacement Is Required

The current architecture stores browser interactions as simplified JSON locators generated inside a custom page script. Replay then translates those locators into Playwright APIs later. This creates semantic drift:

- The recorder chooses selectors using a custom scoring algorithm.
- Replay uses Playwright strict-mode locator semantics.
- Validation mostly happens only at playback time.
- Frame context is not represented in the step model.

Even if the current Python recorder is heavily improved, it would still remain a separate implementation that must continually track Playwright behavior. That is not a stable end state.

The final design therefore adopts Playwright recorder semantics directly, instead of continuing to emulate them.

## Architecture Overview

### High-Level Components

The system is split into the following major components:

1. `rpa-engine` (new, Node + Playwright)
2. Python backend recorder gateway (existing backend, rewritten around engine RPC)
3. Frontend recorder/test/configure UI (existing Vue pages, redesigned state model)
4. Skill exporter and AI orchestration (existing product layer, adapted to V2 actions)

### Ownership Boundaries

The new ownership boundary is strict:

- `rpa-engine` owns:
  - browser launch and lifecycle
  - browser context lifecycle
  - page/tab lifecycle
  - frame tree awareness
  - recorder injection
  - selector generation
  - action recording
  - locator validation
  - playback execution
  - screencast source control

- Python backend owns:
  - authenticated user session lifecycle
  - persistent storage
  - REST and websocket APIs
  - mapping product session IDs to engine session IDs
  - transforming recorded actions into product-facing step models
  - AI assistant integration
  - skill export

The browser runtime must no longer be owned by Python code. Python becomes a controller and product adapter, not the recorder core.

## Runtime Model

### Engine Session

Each engine session owns exactly one browser context and may contain multiple tabs and frames.

Suggested engine-side state:

- `engineSessionId`
- `browser`
- `context`
- `tabs`
- `activeTabId`
- `pagesByTabId`
- `pageAliases`
- `frameTree`
- `recordedActions`
- `recorderMode`
- `validationState`
- `screencastController`

### Tab Model

Each tab contains:

- `tabId`
- `pageAlias`
- `page`
- `url`
- `title`
- `openerTabId`
- `createdAt`
- `lastSeenAt`
- `status`

The active tab is the single source of truth for:

- preview target
- user input target
- execution target
- AI live-session target

### Frame Model

Frames are not stored as independent product objects, but every recorded action must carry enough frame information to re-resolve the target frame.

Each recorded action stores:

- `tabId`
- `framePath`

`framePath` is an ordered list of selectors identifying the chain of `iframe` elements from the tab's main frame to the target frame.

This mirrors Playwright recorder behavior and allows code generation through `frame_locator(...)` or `.content_frame` chains.

## Recorded Action Model

### Source of Truth

The canonical source of truth is `RecordedActionV2`.

The frontend-facing RPA step list is derived from it. Exported skills are derived from it. Test execution is derived from it. AI patches must also eventually compile into it.

### RecordedActionV2 Schema

Each action stores:

- `id`
- `sessionId`
- `tabId`
- `pageAlias`
- `framePath`
- `action`
- `selector`
- `selectorSource`
- `signals`
- `value`
- `modifiers`
- `position`
- `timestamp`
- `locatorCandidates`
- `elementSnapshot`
- `validation`
- `status`

### Action Types

Core supported actions:

- `navigate`
- `click`
- `fill`
- `press`
- `select`
- `check`
- `uncheck`
- `setInputFiles`
- `assertText`
- `assertValue`
- `assertVisible`
- `assertChecked`

Product UI may still describe composite user actions such as "click and open new tab", but those must be rendered from `action + signals`, not stored as separate recorder-truth action types.

### Signals

Signals are part of the action model:

- `navigation`
- `popup`
- `download`
- `dialog`

Example:

- A link click opening a new tab remains `action=click`.
- `signals.popup` contains the target popup metadata.

This avoids rewriting raw actions after the fact and keeps runtime behavior, recorder state, and code generation aligned.

## Locator Model

### Primary and Candidate Locators

A recorded action must not store only one locator. It must store:

- `primaryLocator`
- `locatorCandidates[]`

Each candidate stores:

- `kind`
- `selector`
- `playwrightLocator`
- `score`
- `strictMatchCount`
- `visibleMatchCount`
- `actionability`
- `selected`
- `reason`

### Candidate Ordering

Preferred ordering:

1. `testid`
2. `role+name`
3. `label`
4. `placeholder`
5. `alt`
6. `title`
7. `text`
8. `css`
9. `css scoped by frame`
10. `nth` fallback

### Selection Rules

- `nth` may exist only as a fallback unless no better selector can be validated.
- `label` is never assumed unique without validation.
- `text` is never promoted unless strict resolution proves it is stable enough.
- Test-id-based selectors must preserve the actual test-id attribute policy used by the engine.
- All locator evaluation happens in frame context, never as document-global fallback detached from `framePath`.

## Validation Model

Validation happens during recording, not only during testing.

Every action is validated immediately for:

- strictness
- visibility
- editability or clickability when relevant
- replay resolution in the resolved frame

Validation states:

- `ok`
- `warning`
- `broken`

Examples:

- `ok`: primary locator resolves strictly and is actionable.
- `warning`: primary locator works, but only weak fallbacks exist or target text appears dynamic.
- `broken`: no locator candidate resolves strictly in the expected frame.

Validation artifacts are stored on the action and surfaced in the configure UI.

## Recorder Engine Design

### Why a Separate Node Engine

Playwright recorder internals, selector generation, frame-aware action modeling, and code generation are native to the Node/TypeScript Playwright implementation.

The Python runtime does not expose equivalent recorder internals. Rebuilding them in Python would recreate the same long-term semantic drift the current implementation already suffers from.

For that reason, recorder ownership moves to a dedicated Node service.

### Engine Responsibilities

The engine must provide:

- session creation and shutdown
- local and sandbox browser launch modes
- recorder mode switching
- recording event emission
- page/tab registration
- frame-path resolution
- locator generation and validation
- screencast control
- playback execution
- code generation support metadata

### Engine API Shape

The transport may be JSON-RPC over websocket, local HTTP, or stdio-supervised subprocess RPC. The design does not depend on the transport choice, but the API must expose these commands:

- `create_session`
- `close_session`
- `set_mode`
- `pause_recording`
- `resume_recording`
- `activate_tab`
- `list_tabs`
- `list_actions`
- `validate_action`
- `update_primary_locator`
- `replay_actions`
- `start_screencast`
- `stop_screencast`

The engine must also emit events:

- `tabs_snapshot`
- `frame`
- `recorded_action`
- `action_updated`
- `validation_result`
- `preview_error`
- `execution_log`

## Python Backend Design

### Backend Role

The backend becomes a gateway and orchestration layer around the engine.

New backend responsibilities:

- map product session IDs to engine session IDs
- persist `RecordedActionV2` and derived steps
- expose existing `/api/v1/rpa/...` routes with stable product semantics
- forward websocket events from engine to frontend
- expose action editing APIs to the configure page
- compile recorded actions into skill export format

### Backend Modules

Suggested new Python modules:

- `backend/rpa/engine_client.py`
- `backend/rpa/action_models.py`
- `backend/rpa/action_compiler.py`
- `backend/rpa/exporter_v2.py`

Suggested rewrites or removals:

- remove recorder ownership from `backend/rpa/manager.py`
- replace current `backend/rpa/generator.py`
- replace current `backend/rpa/executor.py`

Suggested retained adapters:

- `backend/route/rpa.py`
- `backend/rpa/skill_exporter.py`
- `backend/rpa/screencast.py` as a thin gateway only if still needed by routing

## Frontend Design

### State Model

Recorder, Test, and Configure pages all subscribe to the same session state model:

- `tabs`
- `activeTabId`
- `actions`
- `selectedActionId`
- `previewFrame`
- `previewError`

### Recorder Page

Displays:

- browser-like tab strip
- main preview canvas
- live action timeline
- active frame breadcrumb
- validation badges

### Test Page

Displays:

- browser-like tab strip
- preview canvas
- execution log
- current action highlight
- validation/fallback information during playback

### Configure Page

Displays per action:

- action summary
- tab context
- frame breadcrumb
- primary locator
- locator candidates
- validation state
- diagnostic details
- control to promote a candidate to primary locator
- revalidate control

The configure page must stop treating each step as a single opaque `target` string.

## AI Assistant Integration

The current split between recorded steps and AI-generated ad hoc Playwright code must be removed.

The AI assistant may:

- add actions
- edit actions
- replace locators
- propose sequence patches

But all AI output must be compiled back into `RecordedActionV2` or a structured patch format that becomes `RecordedActionV2`.

There must no longer be two independent truth models:

- recorded steps
- arbitrary AI scripts

The only truth model is the V2 action graph.

## Code Generation

### General Principle

Code generation consumes validated `RecordedActionV2` records, not the old simplified locator JSON.

### Target Code Shape

Generated Python should use native Playwright semantics and explicit frame chaining.

Example:

```python
tabs = {"tab-1": page}
current_page = page

frame = current_page
frame = frame.frame_locator("iframe[name='workspace']")
frame = frame.frame_locator("iframe[title='editor']")

async with current_page.expect_popup() as popup_info:
    await frame.get_by_role("link", name="Open report").click()
new_page = await popup_info.value
current_page = new_page
tabs["tab-2"] = new_page
```

### Locator Fallback at Runtime

Playback and exported skill code should attempt:

1. primary locator
2. validated secondary candidates
3. scoped CSS fallback
4. `nth` fallback only if explicitly allowed

The playback engine must record when fallback occurs so the UI can recommend primary locator changes.

## Screencast and Active Target

Preview must follow the active tab, while frame context is displayed as metadata and breadcrumb rather than as separate preview channels.

Requirements:

- popup/new-tab activation updates preview automatically
- manual tab activation updates preview automatically
- playback and live recording both resolve targets against the same engine state
- frontend websocket stays stable across tab switches

## Filesystem and Repository Layout

New top-level directory:

- `RpaClaw/rpa-engine/`

Suggested structure:

- `rpa-engine/src/runtime/`
- `rpa-engine/src/recorder/`
- `rpa-engine/src/codegen/`
- `rpa-engine/src/screencast/`
- `rpa-engine/src/rpc/`
- `rpa-engine/src/types/`

This engine should be built and run as part of local/dev Docker composition and local desktop packaging.

## Migration and Cutover

Existing recordings may be discarded. No backward-compatibility bridge is required.

Cutover rules:

- once V2 engine is enabled, V1 recorder routes must no longer create new recordings
- old exported skills may still exist on disk, but new recordings are V2-only
- V1 recorder-specific codepaths should be removed rather than kept indefinitely

## Risks

- Introducing a Node engine adds one more runtime process to manage.
- Packaging for local desktop and Docker must include the engine cleanly.
- Engine/backend protocol design must avoid event ordering bugs.
- The frontend configure page becomes materially more complex because it now exposes diagnostics and locator candidate editing.

These risks are acceptable because they replace a structurally incorrect recorder core with one whose semantics match the execution platform.

## Testing Strategy

### Engine Tests

- single-tab recording
- nested iframe recording
- cross-origin iframe recording
- popup/new-tab recording
- download signal capture
- strict locator validation
- fallback locator execution

### Backend Tests

- session to engine lifecycle mapping
- persistence of `RecordedActionV2`
- configure-page locator update APIs
- export pipeline correctness

### Frontend Tests

- tab strip updates from snapshots
- configure page locator promotion
- validation badge rendering
- frame breadcrumb rendering
- playback logs showing fallback behavior

### End-to-End Tests

- record and replay a workflow with nested iframe interaction
- record and replay a popup that contains iframe interaction
- export a skill from a multi-tab, multi-frame session and execute it successfully
- edit a broken primary locator in the configure page and verify replay succeeds without re-recording

## Implementation Order

1. Create `rpa-engine` with session, tab, and frame runtime.
2. Integrate Playwright-recorder-based action recording and validation in the engine.
3. Add backend engine client and session gateway.
4. Replace recorder/test websocket flow to consume engine events.
5. Introduce `RecordedActionV2` persistence and compiler.
6. Redesign configure page around locator candidates and validation.
7. Replace skill export/codegen to consume V2 actions.
8. Move AI assistant edits onto the V2 action model.
9. Delete V1 recorder codepaths.

## Recommendation

Adopt the full recorder-core replacement with a dedicated Node `rpa-engine` and a Python product gateway. This is the cleanest end state and the only design in this codebase that fully eliminates the architectural causes of poor locator quality and missing iframe capture.

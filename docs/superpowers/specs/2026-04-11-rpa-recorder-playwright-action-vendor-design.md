# RPA Recorder Playwright Action Vendor Design

## Goal

Align recorder action classification with Playwright upstream semantics so one user gesture records one logical step instead of a raw DOM event stream.

## Problem

The current recorder capture layer still records browser events directly:

- `click` becomes `click`
- `input` becomes `fill`
- `change` becomes `select` only for native `select`

This diverges from Playwright recorder semantics. A single gesture such as clicking a checkbox can synchronously trigger multiple secondary events on labels, wrappers, and associated controls. The recorder then stores those derived events as separate steps, producing sequences such as:

1. click checkbox
2. click `row("序号") >> label("")`
3. fill `""` into `row("序号") >> label("")`

The same structural problem likely affects radio/switch controls, select-like components, file inputs, keyboard-triggered toggles, and navigation chains with auto-redirects.

## Requirements

- Reuse Playwright upstream recorder action classification logic as much as practical.
- Stop treating raw DOM `click/input/change` events as the final recorded truth.
- Preserve the existing Python event payload contract enough to avoid a larger RPA rewrite.
- Support new logical actions where upstream semantics require them, especially `check`, `uncheck`, and `set_input_files`.
- Reduce duplicate noise for checkbox/radio/select and adjacent redirect/navigation flows.

## Chosen Approach

Vendor a focused Playwright recorder action runtime into `backend/rpa/vendor/` and make the current capture script a thin bridge.

The vendored runtime will adapt the upstream `RecordActionTool` behavior and own:

- active target tracking
- wrong-target filtering
- checkbox/radio toggle classification
- file/range/select input handling
- keypress classification
- suppression of derivative events that are part of the same logical gesture

The existing capture script will keep recorder-specific concerns:

- frame-path collection
- locator bundle generation through the existing vendored selector runtime
- event sequencing and timestamps
- serialization into the current backend event schema

## Non-Goals

- Replacing the Python recorder/session model
- Replacing the configure page
- Rebuilding the full Playwright overlay or inspect/assert tooling
- Migrating old recordings

## Architecture

### Browser-side runtime split

1. `playwright_recorder_runtime.js`
   - existing vendored selector runtime
2. `playwright_recorder_actions.js`
   - new vendored action-classification runtime derived from Playwright recorder semantics
3. `playwright_recorder_capture.js`
   - small adapter that initializes the action runtime and forwards normalized actions through `__rpa_emit`

### Action model

The browser runtime emits logical actions instead of raw event types:

- `click`
- `check`
- `uncheck`
- `fill`
- `select`
- `press`
- `set_input_files`

This keeps the current event schema but changes how `action` is chosen.

### Backend handling

`manager.py` will accept and describe the new actions while preserving existing signal attachment and ordering logic. `generator.py` will map:

- `check` -> Playwright `.check()`
- `uncheck` -> Playwright `.uncheck()`
- `set_input_files` -> Playwright `.set_input_files(...)`

Navigation upgrades should continue to attach to the preceding logical action, not a derived noisy event.

## Testing Strategy

Add regression coverage for:

- capture bundle injection now includes the vendored action runtime hook
- manager descriptions and stored steps support `check` / `uncheck`
- generator emits `.check()` / `.uncheck()`
- checkbox/radio derived-event noise is prevented by the new browser-side contract
- redirect/navigation upgrade still works with the new action set

## Risks

- Vendored runtime drift when upgrading Playwright
- Subtle behavior gaps because we are reusing recorder semantics without the full upstream overlay/action execution stack

## Mitigations

- Keep the vendored scope limited to action classification and document the upstream source
- Add regression tests around the exact adapter contract and generated script output
- Preserve the existing selector runtime and Python payload shape to limit blast radius

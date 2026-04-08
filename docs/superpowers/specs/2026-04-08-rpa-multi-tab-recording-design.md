# RPA Multi-Tab Recording and Playback Design

## Summary

The current RPA recorder assumes one session maps to one Playwright `Page`. When a click opens a new tab, the new tab is neither registered for event capture nor used as the current display or execution target. The recorder therefore stops observing user actions, and the live preview remains stuck on the old page.

This design replaces the single-page session model with a session-scoped multi-tab model. Each RPA session owns one browser context, multiple tracked tabs, one active tab, and one session-level screencast controller. New tabs automatically become active by default. The frontend renders a browser-like tab strip so users can manually switch tabs while keeping recording, display, test execution, and agent execution aligned with the same active tab.

## Implementation Status (2026-04-08)

The current branch now implements the core multi-tab model described below:

- `backend/rpa/manager.py` tracks one session context plus multiple tabs, metadata, and one active tab.
- New pages created by `context.on("page")` are registered, injected, and auto-activated.
- `/api/v1/rpa/session/{session_id}/tabs` and `/api/v1/rpa/session/{session_id}/tabs/{tab_id}/activate` are implemented.
- `backend/rpa/screencast.py` now contains a session-level `SessionScreencastController` that switches the streamed CDP page when the active tab changes.
- Recorder and test pages render a browser-style tab strip from `tabs_snapshot` and support manual tab switching.
- Test execution, assistant chat, and ReAct agent execution now follow the active tab instead of holding one stale page reference.
- Recording upgrades plain `click` steps into `navigate_click` or `open_tab_click` when runtime evidence proves a same-tab navigation or popup/new-tab transition.
- Script generation now uses `tabs` plus `current_page`, supports `open_tab_click` and `switch_tab`, and backfills missing multi-tab semantics for older recordings by inferring transitions from `tab_id` changes.

The following user-reported regressions were fixed during implementation:

- New-tab actions were being recorded but the preview stayed on the old page. The manager now promotes the event source tab to active when events arrive from a non-active tab.
- Link clicks were over-generated as `expect_navigation(...)`. Navigation waiting is now emitted only for `navigate_click`.
- Popup/new-tab clicks could still replay as plain `click()` for older recordings. The generator now infers popup and tab-switch semantics from later `tab_id` changes when explicit step upgrades are missing.

## Goals

- Preserve recording when a user action opens a new tab.
- Automatically switch the live preview and execution target to the new tab.
- Let users manually switch tabs from the recorder and test UI.
- Keep local and cloud recording on one CDP-based tab model.
- Generate stable Playwright scripts for `click -> popup/new tab -> continue`.

## Non-Goals

- Rendering multiple live tab thumbnails in parallel.
- Supporting separate display, recording, and execution targets in v1.
- Reproducing the native Chromium tab bar pixel-for-pixel.

## Current Problem

The current implementation stores a single page per session and binds both event injection and screencast setup to that page only. This breaks when the browser context creates additional pages.

- `RPARecorderManager.create_session()` creates one `context` and one `page`, stores only that page, injects `CAPTURE_JS`, and binds page-level listeners once.
- `/api/v1/rpa/screencast/{session_id}` resolves the current page from the manager once and creates a fixed CDP screencast session for it.
- Assistant chat, ReAct agent execution, and test execution all resolve one page and assume subsequent work stays on that page.

The result is a split-brain state:

- The browser is now interacting with a new page.
- The backend still records and streams the old page.
- The frontend still displays the old page.
- Generated scripts lack explicit tab semantics.

## Proposed Architecture

### Session Model

Each RPA session will track one browser context and many tabs.

New session-scoped structures:

- `contexts[session_id] -> BrowserContext`
- `tabs[session_id] -> {tab_id: Page}`
- `tab_meta[session_id] -> {tab_id: TabMeta}`
- `active_tab_id[session_id] -> str | None`
- `screencast_controller[session_id] -> SessionScreencastController | None`

`TabMeta` will contain:

- `tab_id`
- `title`
- `url`
- `opener_tab_id`
- `created_at`
- `last_seen_at`
- `status` (`open` or `closed`)

In v1, the active tab is the single source of truth for:

- live display target
- user input target
- recording target
- assistant execution target
- test execution target

### Tab Lifecycle

All pages in the browser context must go through a shared registration path:

1. Create the initial page during session startup.
2. Register `context.on("page")`.
3. For each new page, call `register_page(session_id, page, opener_tab_id, make_active=True)`.

`register_page(...)` is responsible for:

- assigning a stable `tab_id`
- storing the page and metadata
- exposing `__rpa_emit`
- injecting `CAPTURE_JS`
- binding page listeners
- updating title and URL metadata
- activating the tab when required
- notifying the frontend of tab changes

### Active Tab Semantics

Default behavior:

- If a user action opens a new tab, the new tab becomes active automatically.
- If the user clicks another tab in the UI, that tab becomes active explicitly.
- If the active tab closes, fallback order is:
  - opener tab if still open
  - most recently seen open tab
  - any remaining open tab

Activation performs:

- `active_tab_id = target_tab_id`
- `await page.bring_to_front()`
- screencast controller switch to the new page
- frontend observes the next `tabs_snapshot`

## Backend Design

### Recorder Manager Changes

`backend/rpa/manager.py` will be refactored from a single-page registry to a multi-tab registry.

New methods:

- `register_page(session_id, page, opener_tab_id=None, make_active=False)`
- `activate_tab(session_id, tab_id, source="auto")`
- `get_active_page(session_id)`
- `list_tabs(session_id)`
- `close_tab(session_id, tab_id)`
- `update_tab_meta(session_id, tab_id, title=None, url=None, status=None)`

Page-level listeners attached during registration:

- `framenavigated`
- `load`
- `download`
- `close`

Context-level listeners:

- `page`

Metadata updates:

- On navigation or load, refresh `url` and `title`.
- On close, mark the tab closed, remove it from the page registry, and activate a fallback tab if necessary.

### Recording Event Model

Every recorded step will include `tab_id`.

New step types:

- `open_tab_click`
- `switch_tab`
- `close_tab`
- `navigate_click`

Rules:

- A click that opens a new tab is upgraded from plain `click` to `open_tab_click`.
- The resulting step stores both `source_tab_id` and `target_tab_id`.
- A manual or automatic activation emits a `switch_tab` step if the effective interaction target changes.
- Normal `fill`, `press`, `click`, `select`, and `navigate` steps remain unchanged except for `tab_id`.
- A click that stays in the same tab but does trigger a real navigation is upgraded from plain `click` to `navigate_click`.

This keeps the timeline understandable for users and gives the generator enough structure to rebuild tab behavior deterministically.

Current implementation notes:

- `open_tab_click`, `navigate_click`, `switch_tab`, and `close_tab` are implemented as first-class runtime steps.
- Generator-side inference is still retained for backward compatibility so older recordings that only reveal tab transitions through later `tab_id` changes continue to replay correctly.

### Session-Level Screencast Controller

`backend/rpa/screencast.py` should move from a fixed page CDP session to a session-scoped controller that can switch pages.

Responsibilities:

- own the frontend websocket
- own the active CDP page session
- start screencast on the active tab
- stop screencast on the previous tab during switches
- forward tab metadata events to the frontend
- keep input injection mapped to the active tab only

Key behavior:

1. Client connects to `/rpa/screencast/{session_id}`.
2. Controller resolves the current active page.
3. Controller creates a CDP session for that page and starts screencast.
4. If `activate_tab()` is called later, the controller detaches from the old page, creates a new CDP session for the new page, and continues sending frames over the same websocket.

This avoids reconnecting the frontend socket on every tab switch.

Current implementation note:

- Session-level switching over one websocket is implemented.
- Retry-on-switch-failure is implemented.
- The controller emits `preview_error` so recorder and test pages can surface temporary preview degradation without dropping the websocket.

### API Changes

Add:

- `GET /api/v1/rpa/session/{session_id}/tabs`
- `POST /api/v1/rpa/session/{session_id}/tabs/{tab_id}/activate`

Adjust:

- `GET /api/v1/rpa/session/{session_id}` to include active tab info when useful
- `/api/v1/rpa/screencast/{session_id}` to act as a session-level stream rather than a page-fixed stream

Websocket messages from screencast stream:

- `frame`
- `tabs_snapshot`
- `tab_created`
- `tab_updated`
- `tab_activated`
- `tab_closed`
- `ping`

Current implementation note:

- The current branch emits `frame`, `tabs_snapshot`, and `ping`.
- Granular `tab_created`, `tab_updated`, `tab_activated`, and `tab_closed` websocket events are still deferred because the frontend currently derives tab state from repeated snapshots.

## Frontend Design

### Recorder and Test UI

Both recorder and test pages will render:

- one browser-style tab strip
- one main canvas preview
- one active URL/title display

The main canvas always shows the active tab. The tab strip is application-rendered from backend metadata rather than relying on Chromium's native tab bar.

### Tab Strip Behavior

Displayed fields per tab:

- title
- active state
- loading or stale placeholder state if needed

User interactions:

- click tab to activate it
- active tab visually highlighted
- newly opened tabs appear immediately and auto-select

The first version does not need a close-tab button in the UI.

### State Flow

Frontend state:

- `tabs`
- `activeTabId`
- `canvas frame`

When websocket events arrive:

- `tabs_snapshot` replaces local tab metadata state
- `frame` redraws the canvas
- `preview_error` updates the inline preview error state

When the user clicks a tab:

1. Frontend calls `POST /tabs/{tab_id}/activate`
2. Backend activates the tab and switches screencast
3. Frontend receives the next `tabs_snapshot`
4. New frames continue on the same canvas

## Execution Design

### Assistant and ReAct Agent

Assistant chat and ReAct agent execution must stop caching one page for the full session.

Instead of resolving one page once at request start, runtime execution should resolve the active page whenever a step is about to run, or use a session handle that updates its current page reference when tab activation changes.

Requirements:

- agent execution follows the active tab
- popup-created tabs can become the current execution target
- display and execution remain aligned

For AI-generated code, prompts should explicitly require `expect_popup()` when an action may open a new tab.

### Script Test Execution

The test executor must treat popup/new-tab transitions as runtime state changes, not as out-of-band browser behavior.

Requirements:

- if execution opens a new tab, the manager updates the active tab
- the screencast controller switches to that tab
- the test page preview follows the tab currently being operated on

### Script Generation

`backend/rpa/generator.py` must stop assuming all recorded steps target the initial `page`.

Generated script pattern:

```python
tabs = {"tab_1": page}
current_page = page
```

For `open_tab_click`:

```python
async with current_page.expect_popup() as popup_info:
    await locator.click()
new_page = await popup_info.value
await new_page.wait_for_load_state("domcontentloaded")
tabs["tab_2"] = new_page
current_page = new_page
```

For `switch_tab`:

```python
current_page = tabs["tab_1"]
await current_page.bring_to_front()
```

Subsequent locators operate on `current_page`, not always on `page`.

This makes generated skills stable for:

- user-recorded multi-tab flows
- agent-augmented multi-tab flows
- test replay

## Error Handling

### Tab Registration Failures

If JS injection or listener registration fails on a new tab:

- keep the tab registered
- mark it degraded in logs
- still allow activation and display
- do not silently drop the page from the registry

### Screencast Switch Failures

If screencast switching fails:

- log the tab transition failure
- keep the websocket alive
- retry once when possible
- send a frontend event so the UI can show that preview is temporarily unavailable

### Closed or Missing Tabs

If the frontend requests activation for a closed or unknown tab:

- return `404` or `400`
- leave the active tab unchanged

If the active tab closes during recording or execution:

- auto-fallback to an open tab
- emit a fresh `tabs_snapshot`

## Testing Strategy

### Backend Tests

- session startup registers the initial tab and marks it active
- `context.on("page")` registers a new tab and activates it automatically
- closing the active tab falls back correctly
- `list_tabs()` returns accurate metadata
- `activate_tab()` updates state and calls `bring_to_front()`
- screencast controller switches target page without dropping the websocket state
- step creation includes `tab_id`
- `open_tab_click` and `switch_tab` serialize correctly
- generator emits `expect_popup()` and `current_page` transitions

### Frontend Tests

- recorder tab strip renders snapshot data
- clicking a tab issues activate API call
- websocket snapshot updates the UI correctly
- auto-created tabs become active visually
- canvas continues rendering after tab switches

### End-to-End Validation

- record a link click that opens a new tab and continue interacting there
- switch back to the original tab manually and continue recording
- run test playback for a multi-tab recording and verify preview follows execution
- run assistant or agent commands that open a new tab and verify display and execution stay aligned
- validate that plain link clicks without real navigation no longer generate `expect_navigation(...)`
- validate that older recordings with later `tab_id` changes still replay as popup/tab-switch flows without requiring a full re-record

## Rollout Plan

1. Refactor manager state to support multi-tab sessions.
2. Add tab APIs and tab metadata websocket events.
3. Replace fixed-page screencast handling with a session-level controller.
4. Add frontend tab strip to recorder and test pages.
5. Update assistant, agent, and test execution to use active-tab semantics.
6. Update script generation for explicit popup and tab-switch steps.
7. Verify multi-tab flows in both local and cloud CDP environments.

## Risks and Tradeoffs

- Session-level screencast switching is more stateful than the current fixed-page model, so lifecycle bugs are possible if cleanup is incomplete.
- Automatically switching to a new tab matches browser expectations, but it means every popup-like page must be considered intentional unless future heuristics say otherwise.
- Recording explicit tab steps adds complexity to the generator, but without them replay will remain fragile.

## Remaining Gaps

1. Screencast websocket updates still use full `tabs_snapshot` payloads rather than granular `tab_created`, `tab_updated`, `tab_activated`, and `tab_closed` events.
   Impact:
   The frontend stays simple and consistent, but the transport is more redundant than an event-delta model.

2. The first version still does not expose a native close-tab control in the recorder or test UI.
   Impact:
   Users can switch among tabs like a normal browser, but closing tabs from the custom tab strip remains out of scope for now.

## Recommendation

Implement the session-scoped multi-tab model as the primary fix. It solves the root cause once across recording, display, testing, and agent execution, and it gives the frontend a clear tab abstraction that matches how users already expect a browser to behave.

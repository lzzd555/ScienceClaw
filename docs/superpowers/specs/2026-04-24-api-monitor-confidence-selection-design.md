# API Monitor Confidence and Selection Design

## Purpose

API Monitor should help users turn captured network traffic into useful MCP tools without letting injected scripts, telemetry, configuration probes, or background requests pollute the final tool set.

The current capture pipeline can filter static resources, non-XHR/fetch requests, cross-origin requests, and duplicate endpoints by URL path. That is not enough for same-origin injected requests. For example, a page-owned business API and an injected same-origin configuration query can share the same host, referer, cookies, and fetch metadata. URL and headers alone cannot reliably distinguish them.

This design keeps capture reversible: collect candidate APIs, attach confidence and evidence, default-select only high-confidence candidates, and publish only user-selected APIs.

## Goals

- Preserve all captured API candidates so users can recover false negatives.
- Mark every generated API candidate with `confidence`, `confidence_reasons`, and `selected`.
- Default `selected=true` only for high-confidence business APIs.
- Let users toggle whether each candidate is adopted.
- Publish MCP tools only for adopted candidates.
- Use request source attribution, not only URL/header heuristics, to handle same-origin injected requests.

## Non-Goals

- Do not permanently delete low-confidence requests during capture.
- Do not build a user-trained rule system in the first version.
- Do not rely on LLM classification for real-time request filtering.
- Do not block recording or analysis on slow post-processing.

## Recommended Approach

Use a candidate-management model:

```text
CapturedApiCall -> grouped API candidate -> confidence classification -> user selection -> MCP publish
```

Every candidate has:

```text
confidence: high | medium | low
selected: boolean
confidence_reasons: string[]
source_evidence:
  initiator_type
  initiator_urls
  js_stack_urls
  frame_url
  action_window_matched
```

Default selection:

```text
high   -> selected = true
medium -> selected = false
low    -> selected = false
```

The conservative principle is: uncertain requests are visible but not adopted by default.

## Request Source Evidence

### CDP Initiator

Subscribe to Chrome DevTools Protocol `Network.requestWillBeSent` for each monitored page. Store initiator metadata keyed by request identity, including:

- `initiator.type`
- stack call frame URLs
- function names and line/column numbers when available
- request timestamp

This identifies whether a request was triggered by page scripts, injected scripts, extensions, eval-like sources, or unknown browser activity.

### Page-Level Fetch/XHR Stack

Inject a lightweight script before page scripts run. It wraps:

- `window.fetch`
- `XMLHttpRequest.prototype.open`
- `XMLHttpRequest.prototype.send`

For each fetch/XHR, record:

- method
- URL
- timestamp
- `Error().stack`
- current frame URL

Then correlate this record with captured requests by method, URL, and a small timestamp window. This complements CDP when initiator stacks are incomplete.

### Action Window

During automatic probing and manual recording, record the current user or probe action timestamp. Requests that occur within a short action window, initially 0-2 seconds after the action, receive a positive confidence signal. Requests outside action windows are not automatically discarded; they are downgraded unless other evidence strongly marks them as business APIs.

## Confidence Classification

Classification is rule-based and explainable in the first version.

High confidence:

- Same origin or explicitly allowed by existing capture rules.
- Occurs inside a user/probe action window.
- CDP initiator or JS stack points to current page business scripts.
- Path or response shape looks like business data, such as `/api`, `/biz`, `/v1`, list/detail/status payloads.

Medium confidence:

- Same origin and action-window matched.
- Initiator or JS stack is missing or incomplete.
- Request and response look plausible, but source cannot be proven.

Low confidence:

- Initiator or stack points to extension, userscript, injected script, eval, generic SDK, or unrelated frame.
- Path looks like configuration, telemetry, logging, heartbeat, tracking, or model/alias probes.
- Request appears periodically or outside action windows.

Each candidate stores short user-facing reasons, for example:

- `Triggered by user action`
- `Initiated by page script`
- `Initiator stack unavailable`
- `Path looks like configuration query`
- `Outside action window`

## Data Model Changes

Extend captured calls or grouped tool definitions with:

```text
confidence: high | medium | low
selected: bool
confidence_reasons: list[str]
source_evidence: dict
```

The preferred boundary is the grouped API candidate/tool-definition level because users decide whether to adopt an endpoint, not individual raw samples. Raw samples can still carry evidence for debugging and scoring.

## Backend Flow

### Capture

1. Keep the existing network filters.
2. Capture all remaining XHR/fetch requests.
3. Attach CDP initiator evidence when available.
4. Attach page-level fetch/XHR stack evidence when available.
5. Attach action-window metadata.

### Candidate Generation

1. Group captured calls using the existing dedup key.
2. Generate candidate tool definitions.
3. Score each candidate from its samples and evidence.
4. Set default `selected` from confidence.
5. Return candidates to the frontend with reasons and evidence summary.

### Selection Updates

Add a backend endpoint to update candidate selection state:

```text
PATCH /api/v1/api-monitor/session/{session_id}/tools/{tool_id}/selection
body: { selected: boolean }
```

This persists the user's choice in the session tool definition.

### MCP Publish

When publishing API Monitor MCP tools, include only candidates with:

```text
selected == true
```

Medium or low confidence candidates are ignored unless the user adopts them.

## Frontend Flow

The API Monitor page shows two groups:

```text
Adopted
Not Adopted
```

Each API row shows:

- method
- URL pattern
- confidence badge
- concise confidence reasons
- selection toggle

Default layout:

- high confidence candidates appear in Adopted.
- medium and low confidence candidates appear in Not Adopted.
- users can move any API between groups with the toggle.

The UI should avoid destructive language. A low-confidence API is not "deleted"; it is simply "not adopted".

## Error Handling

- If CDP initiator capture fails, continue recording and mark source evidence as unavailable.
- If fetch/XHR stack injection fails, continue with CDP and heuristic signals.
- If confidence scoring cannot classify a candidate, mark it `medium` and `selected=false`.
- If selection update fails, keep the current UI state unchanged and show a normal API error.

## Performance

The design avoids additional network requests and avoids LLM calls during capture classification. CDP events and JS stack capture are collected as requests occur. The primary overhead is metadata storage and light rule evaluation.

The existing probing wait can remain unchanged initially. Action-window matching uses existing timing rather than adding extra waits.

## Testing

Backend tests:

- same-origin business request can be high confidence and selected by default.
- same-origin injected/config-like request can be low confidence and not selected by default.
- missing initiator evidence produces medium confidence and not selected by default.
- user selection update persists.
- MCP publishing includes selected candidates only.
- existing URL dedup and origin/static filtering continue to pass.

Frontend tests:

- candidates render in Adopted and Not Adopted groups.
- confidence badges and reasons display.
- toggling selection calls the update endpoint.
- publish flow excludes non-adopted candidates.

## Rollout Plan

1. Add data fields and backend scoring helpers.
2. Add CDP initiator capture.
3. Add page fetch/XHR stack capture and correlation.
4. Apply confidence scoring during candidate generation.
5. Add selection update endpoint.
6. Update MCP publish filtering.
7. Update frontend candidate grouping and toggles.
8. Add tests around classification, selection, and publish filtering.

## Future Extension

A later version can add user-defined rules:

- always adopt matching URL pattern
- never adopt matching URL pattern
- trust specific script bundles
- ignore specific initiator URLs

This should come after the candidate-selection workflow is stable.

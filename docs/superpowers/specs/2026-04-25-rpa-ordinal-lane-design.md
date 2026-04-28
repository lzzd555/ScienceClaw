# RPA Ordinal Lane Design

## Goal

Fix high-confidence ordinal list operations such as:

- get the first item name
- click the second item
- click download on the second item
- extract the first N item names

Do this without degrading the current trace-first recording path that already works for most non-ordinal scenarios.

## Problem Statement

The current recording runtime often collapses ordinal instructions into recording-time observed values. On list-like pages, the compact snapshot tends to foreground one expanded region. The planner then turns instructions such as "the first item" into a concrete title selector instead of a relative action.

This is not only a GitHub Trending problem. The same failure shape appears in internal tables, card lists, and search-result lists where the system does not preserve one clear abstraction:

```text
repeated candidates + stable order + item-scoped actions
```

## Non-Goals

- Do not redesign the global compact snapshot ranking strategy.
- Do not replace the main recording planner.
- Do not broaden runtime AI semantic selection.
- Do not handle ranking semantics such as "most relevant", "highest score", or "best match".
- Do not handle pagination, virtual scrolling, or cross-page aggregation in the first version.

## Design Summary

Add a narrow runtime-only ordinal overlay in `RecordingRuntimeAgent`.

The overlay should:

1. detect explicit ordinal intent
2. extract a repeated candidate collection from `raw_snapshot`
3. build a deterministic ordinal plan when confidence is high
4. otherwise fall back to the existing planner unchanged

This keeps ordinal handling additive and local. It does not rewrite the default behavior for non-ordinal tasks.

## Why This Shape

There are three candidate intervention points:

1. global snapshot compression
2. runtime grounding
3. compiler rewrite

The recommended intervention point is runtime grounding.

Reasons:

- Global snapshot changes would affect all recording tasks and create the highest regression risk.
- Compiler rewrite is too late because the recording-time action may already be grounded to the wrong target.
- Runtime grounding can be narrow, deterministic, and guarded by strict fallback.

## Entry Conditions

The ordinal overlay may take over only when all of the following are true:

1. The instruction contains explicit ordinal intent.
2. The page exposes a high-confidence repeated candidate collection.
3. The task is a deterministic ordinal task supported by the first version.
4. The target can be executed within one item scope without ambiguous page-wide search.

If any condition is not met, the runtime must use the existing planner path.

## Ordinal Intent Scope

Supported first-version ordinal intent:

- first
- second
- last
- nth
- first N

Examples:

- "获取第一个项目的名称"
- "点击第二个项目"
- "点击第二项名字进行下载"
- "获取前5项的名字"

The overlay should not take over tasks that merely mention multiple items but still require semantic reasoning or comparison.

## Supported Task Scope

The first version should support only these deterministic operations:

- extract the title or visible main text from the nth item
- click the primary action of the nth item
- click an explicit secondary action inside the nth item, such as download
- extract the first N titles as an array

The first version should reject or fall back for:

- summarize first N items
- compare first two items
- choose the most relevant among first N
- find the row containing a fuzzy text target
- operate across pagination

## Runtime Data Model

Do not introduce a new persisted trace contract in the first version.

Use a runtime-only candidate collection view:

```python
{
    "kind": "repeated_candidates",
    "source": "raw_snapshot",
    "items": [
        {
            "index": 0,
            "title": "Example",
            "item_locator": {...},
            "primary_action": {...},
            "secondary_actions": [
                {"action": "download", "locator": {...}}
            ],
        }
    ],
}
```

Key properties:

- `item_locator` defines the item scope first
- `primary_action` is the default item entry action
- `secondary_actions` are item-local actions only

The overlay must not execute page-global actions when it claims to act on the nth item.

## Candidate Extraction Principles

Candidate extraction should use `raw_snapshot`, not only `compact_snapshot`.

The extractor should look for repeated sibling structures that behave like:

- table rows
- record cards
- search result entries
- list items with repeated action patterns

The extractor must prefer stable repeated structures over ad hoc text scanning.

The extractor should emit confidence failure rather than guessing when:

- item grouping is unstable
- item order is unclear
- the page appears virtualized or truncated
- secondary actions cannot be bound to a single item

## Deterministic Plan Building

When the overlay takes over, it should build a deterministic plan rather than asking the LLM to invent selectors.

Examples:

- first item name -> read `items[0].title`
- second item click -> click `items[1].primary_action`
- second item download -> click `items[1].secondary_actions["download"]`
- first N names -> return `[item.title for item in items[:N]]`

The generated recording trace should reflect ordinal grounding, not the observed title as the primary selector.

## Strict Fallback

Strict fallback is required in the first version.

Fallback must happen when:

- ordinal intent is absent or ambiguous
- repeated candidates are absent or low-confidence
- nth target index is out of range
- target item scope is unstable
- requested secondary action is not confidently bound to the target item
- the task exceeds the first-version deterministic scope

This rule exists to protect current successful scenarios. The new overlay must be allowed to help only when it is more reliable than the existing planner.

## Module Boundaries

### `backend/rpa/recording_runtime_agent.py`

Add the runtime ordinal overlay here.

Responsibilities:

- detect ordinal intent
- call repeated-candidate extraction
- build deterministic ordinal plans
- decide fallback
- write diagnostics explaining whether ordinal takeover happened

### `backend/rpa/snapshot_compression.py`

Add or reuse helper functions for repeated-candidate extraction only.

Responsibilities:

- expose reusable row/card/list grouping helpers
- avoid changing default compact snapshot ranking or expansion behavior

### Tests

Add focused tests for:

- ordinal success paths
- ordinal fallback paths
- non-ordinal regression protection

## Explicitly Not Changed In Phase 1

- `backend/rpa/trace_skill_compiler.py` main routing
- global `compact_recording_snapshot()` tiering behavior
- frontend recorder/configure display flow
- repair architecture
- semantic ranking tasks

## Diagnostics

Add explicit debug evidence for ordinal handling:

- whether ordinal intent matched
- whether repeated candidates were extracted
- candidate count
- selected index
- requested action
- fallback reason when takeover did not happen

This should be stored alongside existing recording diagnostics so failures can be explained from evidence.

## Rollout Safety

Recommended protections:

- gate ordinal takeover behind strict fallback from day one
- keep the old planner as the default path
- make ordinal handling additive rather than substitutive
- optionally add a feature flag if early rollout needs a fast off-switch

## Acceptance Criteria

The first version is successful if:

1. "获取第一个项目的名称" no longer hardcodes the observed title as the primary target.
2. "点击第二个项目" uses item order instead of a recorded title match.
3. "获取前5项的名字" returns the first five item titles from a repeated collection.
4. "点击第二项名字进行下载" only takes over when the item-local download action is explicit and stable; otherwise it falls back.
5. Existing non-ordinal tasks such as semantic repository selection, highest-star selection, form extraction, and ordinary navigation do not regress.

## Implementation Order

1. ordinal intent detection
2. repeated candidate extraction
3. deterministic ordinal plan building
4. strict fallback and diagnostics
5. focused regression tests

## Decision

Implement a narrow runtime-only ordinal overlay with strict fallback.

Do not broaden the overlay until the first-version deterministic ordinal tasks are stable on both public list pages and internal table pages.

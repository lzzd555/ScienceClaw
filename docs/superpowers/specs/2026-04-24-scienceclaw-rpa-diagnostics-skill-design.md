# ScienceClaw RPA Diagnostics Skill Design

## Purpose

Create a **project-specific Codex skill** for ScienceClaw that teaches an internal agent how to diagnose RPA recording failures using the project's existing local diagnostics artifacts.

This skill is intended for **internal-network deployment** where the user often cannot export page data or interactively share full browser state. The skill must help the agent reason from local evidence already written to disk:

- `raw_snapshot`
- `compact_snapshot`
- `snapshot_metrics`
- `snapshot_comparison`
- attempt dumps
- generated code dumps
- repair dumps
- console/error logs

The skill should not try to replace the RPA runtime or planner. It should teach the agent how to **classify the failure layer and produce a concrete next-step checklist**.

## Scope

This skill is **ScienceClaw-specific**, not a generic RPA debugging skill.

It should assume the current ScienceClaw architecture and naming:

- Trace-first recording
- `RecordingRuntimeAgent`
- `raw_snapshot` vs `compact_snapshot`
- `snapshot_metrics`
- `snapshot_comparison`
- `failure_analysis`
- `attempt` / `code` / `snapshot` dump files
- `RPA_RECORDING_DEBUG_SNAPSHOT_DIR`

It should be installed directly into the local Codex skills directory:

`C:\Users\HUAWEI\.codex\skills\scienceclaw-rpa-diagnostics`

It should **not** depend on repository-relative loading to be usable.

## Triggering

The skill should be designed for **automatic trigger**, not only manual invocation.

Its metadata description should strongly match these situations:

- RPA recording failed
- extraction result is wrong
- planner selected the wrong region
- raw snapshot may be missing required information
- compact snapshot may have dropped required information
- repair succeeded or failed but the reason is unclear
- dump files exist and need to be analyzed
- dump files do not exist and the agent must first verify whether diagnostics were enabled

## First-Version Shape

The first version should be a **process skill**, not a tooling-heavy skill.

Recommended structure:

```text
scienceclaw-rpa-diagnostics/
  SKILL.md
  references/
    dump-schema.md
```

The first version should **not** include bundled scripts. A future version may add a summarizer script, but the first version should stay lightweight and procedural.

## Core Workflow

The skill should force the diagnosing agent through a fixed order:

1. Confirm whether diagnostics dumps exist at all.
2. If no dumps exist, check whether diagnostics were enabled and whether the relevant session directory exists.
3. If dumps exist, inspect snapshot dumps first.
4. Compare `raw_snapshot` and `compact_snapshot` before blaming planner behavior.
5. Inspect attempt dump and generated code.
6. If repair files exist, compare initial vs repair evidence.
7. Produce:
   - a layered diagnosis
   - a concrete next-action checklist

The skill should explicitly prevent the common anti-pattern:

```text
No direct planner/prompt blame before comparing raw_snapshot and compact_snapshot.
```

## Diagnosis Categories

The skill should standardize output into one primary diagnosis layer.

Required categories:

- `missing_in_raw`
  - target information is absent from `raw_snapshot`
  - likely Snapshot V2 capture problem, selector coverage problem, or capture truncation

- `missing_in_compact`
  - target information exists in `raw_snapshot` but is missing or underrepresented in `compact_snapshot`
  - likely region construction, region tiering, or budget/compression issue

- `planner_missed_present_context`
  - the relevant context exists in `compact_snapshot`, but planner-generated code still targets the wrong region or wrong strategy

- `execution_or_locator_issue`
  - plan direction is reasonable, but generated code, locator choice, actionability, navigation timing, or runtime execution caused failure

- `insufficient_evidence`
  - diagnostics are missing or too incomplete to confidently classify the failure

## Required Evidence Order

The skill should teach the agent to inspect evidence in this order:

### 1. Presence and environment

- Is `RPA_RECORDING_DEBUG_SNAPSHOT_DIR` enabled?
- Does a session directory exist?
- Are there `snapshot`, `attempt`, and `code` files?
- Are there both `initial` and `repair` artifacts?

### 2. Snapshot evidence

Read `snapshot-*-*.json` and focus on:

- `raw_snapshot`
- `compact_snapshot`
- `snapshot_metrics`
- `snapshot_comparison`
- `failure_analysis` when present

### 3. Plan and code evidence

Read `attempt-*-*.json` and paired `code-*-*.py`:

- plan description
- generated code
- execution result
- failure analysis

### 4. Repair evidence

If repair files exist:

- compare initial snapshot vs repair snapshot
- compare initial code vs repair code
- determine whether repair changed the page state, the plan, or only the locator/action strategy

## Required Output Template

The skill should force the agent to end with both a diagnosis and an action list.

Required response shape:

```text
Diagnosis
- layer:
- confidence:
- evidence:
- why:

Next Actions
1.
2.
3.
```

Where:

- `layer` must be one of the standardized categories
- `confidence` should be plain language such as `high`, `medium`, `low`
- `evidence` should point to concrete dump files and fields
- `why` should explain the causal reasoning briefly

## Behavior When Dumps Are Missing

The skill must still be useful when no dump exists.

In that case it should:

1. State that evidence is incomplete.
2. Check whether diagnostics were enabled.
3. Tell the user what directory or environment variable to verify.
4. Explain what evidence must be collected in the next reproduction.
5. Avoid pretending to know the exact layer.

The skill should explicitly prefer:

```text
"insufficient_evidence"
```

over speculative root-cause claims.

## Dump File Semantics

The paired `references/dump-schema.md` should explain the current dump families:

- `*-snapshot-*.json`
  - page-state evidence and snapshot comparison
- `*-attempt-*.json`
  - planner output, execution result, failure classification
- `*-code-*.py`
  - generated runtime script for direct inspection

It should also explain the main high-value fields:

- `raw_snapshot`
- `compact_snapshot`
- `snapshot_metrics.raw_snapshot.content_node_limit_hit`
- `snapshot_metrics.raw_snapshot.actionable_node_limit_hit`
- `snapshot_comparison.classification`
- `failure_analysis.type`
- `execution_result.success`
- `execution_result.error`

## Naming and Conventions

The skill should use ScienceClaw-native terminology and avoid generic advice that is not grounded in this codebase.

It should explicitly mention:

- Trace-first recording
- one repair attempt maximum
- compact snapshot is a presentation layer, not the final semantic decision layer
- raw-vs-compact comparison comes before planner criticism

## Non-Goals

The first version should not:

- automatically patch code
- rewrite planner prompts
- invent missing dump data
- rely on screenshots as the primary evidence source
- introduce new diagnostics infrastructure
- summarize unrelated backend subsystems

## Implementation Plan Boundary

The first implementation should create:

1. `SKILL.md`
2. `references/dump-schema.md`
3. automatic-trigger-oriented metadata and description

It should not add scripts unless later usage shows that the process-only version is too cumbersome.

## Recommendation

Build the first version as a **lean, project-specific, automatically-triggered process skill** with strong diagnosis categories and a strict evidence order. This matches the current ScienceClaw diagnostics system, keeps context cost low, and gives internal-network agents a repeatable way to localize failures without pretending to have stronger evidence than the dump files actually provide.

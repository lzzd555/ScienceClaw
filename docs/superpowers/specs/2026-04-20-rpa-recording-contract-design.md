# RPA Recording Contract Redesign

Date: 2026-04-20

## Goal

Redesign the RPA recording pipeline so that recording-time extraction, runtime context persistence, frontend execution logs, and playback code generation all share one explicit contract.

This redesign targets three current failures:

1. Retry attempts are not modeled explicitly, so the frontend can show `Retrying` while the final result is actually successful.
2. Multi-field extraction cannot reliably persist structured values into context, and the frontend cannot show which keys were written and what values they received.
3. Generated playback code hardcodes recorded page values and brittle locators, which defeats parameterization and makes context rebuild unreliable.

## Scope

This work covers the full pipeline for RPA recording assistant steps:

- Recording-time assistant execution
- Retry and execution status events
- Context ledger writes
- Frontend execution log rendering
- Generated playback code and rebuild logic

This redesign does not attempt to rebuild the entire automation platform or replace the existing agent architecture.

## Product Decisions

The following product decisions are fixed for this redesign:

- `extract_text` remains in the system, but only for temporary page reading that does not write to runtime context.
- Any extraction that writes context, whether single-field or multi-field, must be modeled as `ai_script`.
- Retry history must be fully visible to the frontend. The UI should show each attempt, each failure, and the final recovery or failure result.
- Single-field context extraction and multi-field context extraction must use the same structured output contract.

## Problems In Current System

### 1. Execution Status Is Collapsed

The backend currently emits a final `result.success` boolean plus free-form retry text. This makes the frontend infer execution flow from text instead of reading a structured attempt timeline.

As a result:

- first-attempt failures are not represented as first-class events
- successful retry recovery is indistinguishable from immediate success
- user-visible logs can look contradictory

### 2. Context Writes Are Key-Only, Not Value-Aware

Current recording-time context promotion is based on `result_key`, `result_keys`, and `context_writes`, but the saved step model does not reliably preserve a structured payload for multi-field extraction.

As a result:

- multi-field extraction can print JSON output without actually producing usable context state
- the frontend can list keys, but not the concrete values that were written
- the generator cannot safely distinguish single-value extraction from structured extraction

### 3. Playback Generation Replays Recorded Values Instead Of Intent

Generated code uses recorded URLs, recorded texts, recorded values, and brittle text locators such as `"购买人 李雨晨"` when rebuilding context or replaying extraction.

As a result:

- generated code loses the semantic meaning of parameters
- context rebuild depends on exact recorded page content
- playback becomes fragile and environment-specific

## Design Summary

The redesign introduces one explicit extraction contract and one explicit execution-attempt model.

### Extraction Model

Two extraction modes remain, with strict semantics:

- `extract_text`
  - reads display text for the current interaction only
  - does not write runtime context
  - does not declare `result_key` or `result_keys`
- `ai_script`
  - performs all context-producing extraction
  - may extract one field or many fields
  - must return a structured JSON object
  - may declare one or more context bindings

### Execution Model

Each assistant action execution is represented as a sequence of explicit attempts. Every attempt emits structured events and produces a structured result or failure. The final step result is derived from the full attempt history, not from a single boolean.

### Generation Model

Playback code generation consumes the saved semantic contract, not the recorded raw output. Rebuild logic reconstructs page state and extraction intent, rather than replaying recorded values.

## New Step Contract

For any context-producing extraction step, the saved step payload must include the following conceptual fields:

- `action`
- `description`
- `prompt`
- `output_schema`
- `output_payload`
- `context_bindings`
- `context_reads`
- `context_writes`
- `extraction_source`
- `attempt_summary`

### Field Definitions

#### `output_schema`

Declares the expected structured result shape.

Example:

```json
{
  "buyer": "string",
  "department": "string",
  "acceptor": "string",
  "supplier": "string",
  "expected_arrival_date": "string"
}
```

#### `output_payload`

Stores the actual structured extraction result returned by the successful attempt.

Example:

```json
{
  "buyer": "李雨晨",
  "department": "研发效能组",
  "acceptor": "张雪",
  "supplier": "联想华南直营服务中心",
  "expected_arrival_date": "2026-04-18"
}
```

#### `context_bindings`

Lists which fields from `output_payload` are written into runtime context.

Example:

```json
["buyer", "department", "acceptor", "supplier", "expected_arrival_date"]
```

#### `extraction_source`

Captures the semantic source of extraction, such as page section, field group, or extraction strategy. This is used for debugging, replay generation, and future repair logic.

#### `attempt_summary`

Stores normalized information derived from the attempt timeline, such as:

- attempt count
- whether recovery required retry
- final status
- failure categories encountered before recovery

## Execution Attempt Contract

The backend must emit structured attempt events for every assistant execution.

### Required Event Types

- `attempt_started`
- `attempt_output`
- `attempt_failed`
- `attempt_succeeded`
- `result`
- `done`

### Event Semantics

#### `attempt_started`

Emitted at the start of each execution attempt.

Carries:

- attempt index
- action kind
- short description

#### `attempt_output`

Emitted when the backend has a concrete plan for that attempt.

For `ai_script`, this includes:

- script summary or structured extraction summary
- expected output keys

#### `attempt_failed`

Emitted whenever an attempt fails.

Carries:

- attempt index
- failure category
- raw error
- whether retry will proceed

#### `attempt_succeeded`

Emitted when an attempt completes successfully.

Carries:

- attempt index
- final structured output payload
- context writes with concrete values

#### `result`

Represents the final conclusion after all attempts.

Allowed statuses:

- `success`
- `failed`
- `recovered_after_retry`
- `partial_success`

`partial_success` is required for structured extraction when some requested fields are produced and others are missing or invalid.

## Frontend UX Contract

The frontend must render the attempt timeline directly from structured events instead of inferring state from assistant text.

### Log Presentation Rules

- each attempt is displayed as its own log unit
- failed attempts remain visible after later recovery
- retry recovery is shown explicitly, not silently collapsed into generic success
- final context writes are rendered as key-value pairs

### Example User-Facing Output

For a successful retry flow:

1. Attempt 1 started
2. Attempt 1 failed: execution error
3. Attempt 2 started
4. Attempt 2 succeeded
5. Final status: `成功（经历 1 次重试）`

For successful context extraction:

- `写入上下文: buyer = 李雨晨`
- `写入上下文: department = 研发效能组`

The frontend should no longer show raw placeholder text as the only execution state indicator.

## Context Ledger Redesign

The context ledger must store structured values, not only key presence.

### Required Behavior

- single-field and multi-field context extraction both promote from `output_payload`
- `context_writes` is derived from `context_bindings`, not guessed from loosely coupled fields
- each context write includes both key and concrete value
- ledger entries preserve source step identity and extraction source metadata

### Promotion Rules

- `extract_text` never promotes values into ledger
- `ai_script` may promote values only if they appear in both:
  - `output_payload`
  - `context_bindings`
- missing keys referenced by `context_bindings` are a contract failure, not silent success

## Code Generation Redesign

Generated playback code must compile semantic extraction intent, not replay recorded observed values.

### Generation Rules

#### 1. `rebuild_context` Rebuilds Preconditions Only

`rebuild_context` is responsible for getting the page to the right semantic state before extraction.

It must not:

- hardcode previously extracted values as locators
- hardcode recorded page text as stable identifiers

It may:

- navigate to prerequisite pages
- perform prerequisite actions
- invoke semantic extraction logic using declared source and schema

#### 2. Extraction Code Uses Semantic Targets

Generated extraction logic should preserve:

- section semantics
- field semantics
- parameter semantics

Generated extraction logic should not preserve:

- recorded result values
- brittle text blobs copied from the recorded DOM
- environment-specific URLs unless explicitly modeled as parameters or fixed navigation prerequisites

#### 3. Parameterization Wins Over Hardcoding

Whenever a value belongs to business input, runtime context, or user-supplied arguments, generation must prefer:

- `context`
- `kwargs`
- declared parameters

over embedding the recorded literal into generated code.

## Error Model

Execution errors must be normalized into explicit categories.

### Categories

- `planning_error`
  - assistant output is invalid or unusable
- `execution_error`
  - script fails at runtime, locator fails, page state is wrong
- `contract_error`
  - execution returned data that does not satisfy declared `output_schema` or `context_bindings`

### Retry Policy

- `planning_error`
  - may retry generation
- `execution_error`
  - may retry execution with error feedback
- `contract_error`
  - should be treated as a system bug or generation defect, not silently hidden by free-form retry text

## Migration Strategy

This redesign should be rolled out in four stages.

### Stage 1. Introduce New Contracts

- extend step schema to support structured output and attempt summaries
- add new backend event types
- keep old fields temporarily for compatibility

### Stage 2. Move Context-Producing Extraction To `ai_script`

- prevent `extract_text` from writing context
- require structured output for context-producing extraction
- promote context from `output_payload`

### Stage 3. Upgrade Frontend Attempt Timeline

- render structured attempt events
- render retry history and recovery
- render key-value context writes

### Stage 4. Rewrite Generator Consumption Path

- generate playback from semantic extraction contract
- remove value-based hardcoded extraction patterns
- reduce dependence on recorded raw DOM text

## Testing Strategy

### 1. Runtime Unit Tests

Add tests for:

- single-field `ai_script` extraction producing structured payload
- multi-field `ai_script` extraction producing structured payload
- ledger promotion from `output_payload`
- contract failure when `context_bindings` reference missing keys

### 2. Attempt Event Tests

Add tests for:

- first attempt fails, second succeeds
- final result is `recovered_after_retry`
- frontend receives both failed and successful attempts in order

### 3. Generator Tests

Add tests to ensure generated code:

- does not embed recorded extracted values as locators
- rebuilds context through semantic prerequisites
- parameterizes runtime values correctly

### 4. End-to-End Regression Tests

Use the PR core field scenario as a fixed regression suite for:

- single-field context extraction
- multi-field context extraction
- retry recovery visibility
- frontend key-value context write rendering

## Risks

### Backward Compatibility Risk

Older recordings may only contain `result_key`, `result_keys`, and legacy extraction semantics. A compatibility layer will be needed during migration.

### Scope Risk

This touches backend execution, frontend logs, context storage, and generation. The work should be staged to avoid mixed-contract states remaining in production for too long.

### False Compatibility Risk

Keeping legacy `extract_text` semantics too long will recreate the current ambiguity. Compatibility should be transitional, not permanent.

## Recommended Outcome

Adopt a contract where:

- `extract_text` is read-only and non-contextual
- all context-producing extraction is structured `ai_script`
- retries are explicit attempt timelines
- context writes are key-value aware
- generated playback code compiles semantic intent instead of recorded literal values

This is the smallest redesign that solves the three reported failures as one system problem instead of treating them as isolated bugs.

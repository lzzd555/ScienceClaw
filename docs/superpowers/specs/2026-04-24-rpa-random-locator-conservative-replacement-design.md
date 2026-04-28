# RPA Random Locator Conservative Replacement Design

Branch: `codex/rpa-trace-first-recording`

Date: `2026-04-24`

## 1. Summary

This design adds a **narrow compiler-only stability improvement** for recorded actions whose replay locator depends on likely-random DOM attributes such as dynamic `id`, `class`, or `data-*` values.

The goal is deliberately small:

- keep the current recording execution path unchanged
- record a small amount of additional stability evidence during recording
- let the compiler replace the original locator **only when**:
  - the original locator appears to depend on unstable random-like attributes
  - a clearly more stable replacement is available
- otherwise preserve the current replay output exactly

This work does **not** attempt to solve broader trace generalization problems such as:

- ordinal extraction like "the first project"
- semantic selection
- runtime AI to deterministic replay conversion
- site-specific GitHub optimization

## 2. Background

Current recording behavior is mostly good:

- the recording path can successfully complete many real user steps
- the accepted trace preserves the execution fact that actually worked
- compilation already has a safe fallback: embedded recorded AI code

The problem addressed here is narrower:

- some successful recorded clicks rely on locators containing random-like `id`, `class`, or `data-*` attribute values
- those locators may work only for the recording-time page instance
- the page may still expose stable semantics such as:
  - button text
  - `aria-label`
  - accessible role
  - nearby stable card title or section heading

Today that stability evidence is not systematically preserved for compiler use, so replay can inherit the unstable implementation detail.

## 3. Problem Statement

For a subset of recorded interactive actions, the system records a locator that works during recording but is not stable enough for replay because it depends on random-like DOM attributes.

This creates a specific failure mode:

1. the recording step succeeds
2. the accepted trace keeps the successful recorded locator
3. replay reuses that locator
4. the target element can no longer be found because the random-like attribute changed

The architecture problem is **not** that all locators are wrong.

The architecture problem is that for this narrow class of steps, the compiler currently lacks a safe way to distinguish:

- unstable implementation detail
- stable semantic target evidence

## 4. Goals

- Improve replay stability for steps whose original locator depends on likely-random `id`, `class`, or `data-*`.
- Keep recording-time success behavior unchanged.
- Keep the solution conservative: prefer false negatives over false positives.
- Restrict replacement scope to:
  - stable self signals on the target element
  - at most one stable container anchor
- Preserve the current fallback path whenever confidence is not high.
- Keep diagnostics explainable: the system should be able to say why a locator was replaced or preserved.

## 5. Non-goals

- Do not redesign the recording runtime prompt or planner contract.
- Do not require typed IR for all traces.
- Do not solve ordinal extraction or collection semantics in this work.
- Do not add site-specific GitHub rules.
- Do not add runtime self-healing or execution-time retry heuristics.
- Do not replace locators that are merely ugly; only target likely-random attribute dependence.

## 6. Recommended Approach

### 6.1 Core Decision

Use a **record-more, replace-conservatively** strategy:

- recording continues to execute exactly as it does today
- recording stores extra evidence about stable target semantics and nearby stable anchors
- compilation inspects the original locator for likely-random attribute dependence
- compilation replaces the locator only when a single clearly more stable alternative can be constructed
- otherwise compilation keeps the original locator unchanged

### 6.2 Why This Approach

This is the smallest change that improves the target problem without destabilizing the current successful recording flow.

It avoids two bad extremes:

- **pure cleanup rules on the original selector**
  - too easy to overfit or mis-handle valid numeric attributes
- **broad trace redesign**
  - too invasive for a problem the user currently sees as narrow and secondary

## 7. Data To Add During Recording

This design keeps the current accepted trace intact and adds **optional metadata** for interactive actions.

Suggested fields:

- `primary_locator`
  - the actual locator used by the successful recorded action
- `stable_self_signals`
  - element-level stable evidence such as:
    - role
    - accessible name
    - `aria-label`
    - visible text
- `stable_anchor_signals`
  - nearest stable container context such as:
    - card title
    - section heading
    - row title
- `unstable_signals`
  - any suspicious random-like fragments observed in the original locator, limited to:
    - `id`
    - `class`
    - `data-*`
- `alternate_locators`
  - a short list of candidate stable locators derived from self signals or anchored scope

Important rules:

- these fields are **advisory metadata**, not recording success requirements
- missing metadata must not cause the step to fail
- current `ai_execution.code` and current trace storage remain the primary factual execution evidence

## 8. Compiler Behavior

### 8.1 Replacement Gate

The compiler should only consider replacement when **both** conditions hold:

1. the original locator is classified as likely depending on random-like `id`, `class`, or `data-*`
2. there is exactly one high-confidence stable replacement candidate

If either condition fails, preserve the original locator.

### 8.2 Allowed Replacement Sources

Replacement may use:

- stable self signals on the target element
- plus at most one stable container anchor

Examples of allowed shapes:

- target element only:
  - `get_by_role(..., name=...)`
  - `get_by_label(...)`
  - `get_by_text(...)`
- anchored target:
  - locate stable card or section by heading/title
  - then locate the target inside that scope by role/name

Replacement must **not**:

- search arbitrarily many ancestor levels
- invent semantic intent not present in evidence
- switch to runtime AI
- broaden into multi-candidate execution logic

### 8.3 Selection Priority

When replacement is allowed, choose the most stable candidate in this order:

1. target self signal with clear accessible semantics
2. self signal scoped by one stable anchor
3. preserve original locator

This means the compiler should only move from unanchored to anchored matching when name collisions make the self signal ambiguous.

## 9. Random-like Attribute Detection

The detector must stay narrow.

The first version should only flag attributes when all of the following are true:

- the source attribute is `id`, `class`, or `data-*`
- the matched token contains a strong random-like pattern such as:
  - long mixed alphanumeric fragments
  - hash-like suffixes
  - framework-generated repeated prefixes plus changing numeric/alphanumeric tails
- there is no stronger evidence that the value is a stable business identifier

The detector should **not** treat every number as random.

Examples that should remain conservative:

- tab indices that are stable within product semantics
- numeric business ids that are visible and user-meaningful
- short semantic classes that include numbers but are product-defined

When unsure, classify as **not replaceable**.

## 10. Fallback Policy

Fallback is the most important safety property in this design.

If any of the following is true, keep the original locator:

- no unstable random-like dependency detected
- no stable replacement candidate exists
- more than one replacement candidate is plausible
- stable anchor evidence is weak or missing
- replacement would require broader context than one container anchor

In short:

```text
high confidence replace, otherwise preserve
```

## 11. Product And Diagnostics Behavior

No user-facing recording flow changes are required in the first phase.

Recommended internal diagnostics:

- whether the original locator was marked random-like
- which stable candidates were considered
- why replacement was accepted or rejected

This keeps future debugging clear without changing the current recorder UX.

## 12. Alternatives Considered

### 12.1 Pure Selector Cleanup Rules

Remove or regex-rewrite numeric fragments directly inside recorded selectors.

Pros:

- smallest apparent code change

Cons:

- high false-positive risk
- patch-driven
- weak explainability
- does not use the stable semantic evidence the page already exposes

Decision:

Rejected.

### 12.2 Full Typed IR Redesign

Introduce broad intent/evidence IR and make compiler generation depend on it by default.

Pros:

- stronger long-term architecture

Cons:

- too broad for the current narrow problem
- higher regression risk
- unnecessary disruption to a recording path that is already working reasonably well

Decision:

Deferred.

### 12.3 Runtime Retry Or Self-healing

Keep compilation unchanged and recover only after replay failure.

Pros:

- avoids changing compile output initially

Cons:

- shifts instability to runtime
- harder to explain
- weaker user trust in deterministic replay

Decision:

Rejected for this phase.

## 13. Implementation Outline

### Phase 1: Recording metadata

- extend interactive action traces with optional stability metadata
- keep existing execution and acceptance behavior unchanged

### Phase 2: Conservative detector

- add narrow random-like attribute classification for `id`, `class`, and `data-*`
- add strong default bias toward "not replaceable"

### Phase 3: Compiler candidate construction

- build candidates from:
  - stable self signals
  - one optional stable anchor
- rank candidates conservatively

### Phase 4: Safe replacement

- replace only when one high-confidence candidate clearly beats the original random-like locator
- otherwise preserve the existing compiled output

### Phase 5: Diagnostics

- log replacement decisions for debugging and future refinement

## 14. Test Strategy

### 14.1 Unit Tests

- random-like detector flags hash-like `id/class/data-*`
- detector does not flag ordinary semantic numeric values
- candidate ranking prefers self signal over broader anchored candidates
- ambiguous candidates cause fallback

### 14.2 Integration Tests

- record a click whose original locator contains random-like `id`
- preserve replay when no stable alternative exists
- replace replay when stable `role/name` exists
- replace replay when stable `role/name` requires one stable card/section anchor
- verify unrelated non-random locator scenarios remain unchanged

### 14.3 Regression Guard

Add explicit tests proving the compiler does **not** alter:

- non-random locator steps
- collection/ordinal extraction steps
- runtime semantic AI steps

## 15. Risks

### 15.1 False Positive Random Detection

If the detector misclassifies a stable attribute as random-like, replay could be changed unnecessarily.

Mitigation:

- keep the detector narrow
- require stronger replacement confidence than detection confidence

### 15.2 Over-eager Replacement

A "cleaner" candidate may still be less correct than the original recorded locator.

Mitigation:

- require a single high-confidence winner
- keep fallback to original locator as default

### 15.3 Scope Creep

This work could easily drift into broader generalization efforts.

Mitigation:

- explicitly restrict the change to random-like `id/class/data-*`
- reject changes that also try to solve ordinal or semantic selection

## 16. Final Recommendation

Implement a **small, compiler-focused conservative replacement path** for recorded locators that depend on likely-random `id`, `class`, or `data-*`.

Do not redesign the full trace model in this phase.

Do not change recording success behavior.

Do not broaden to other generalization problems.

Use additional recording metadata only as optional evidence, and replace the original locator only when confidence is high enough that preserving the current locator is clearly worse.

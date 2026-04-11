# RPA Recorder Playwright Selector Vendor Design

## Goal

Align recorder locator generation and candidate validation with Playwright upstream semantics so recorded primary locators match runtime behavior.

## Problem

The recorder currently maintains a custom selector approximation inside `backend/rpa/manager.py`. It diverges from Playwright semantics in critical cases such as anchors without `href`, causing the recorder to promote locators like `get_by_role("link", name="操作")` even though Playwright would not treat the element as a link.

The current uniqueness checks (`testUnique`, `matchesCandidate`, `matchesLocator`) are also recorder-local approximations. They are not reliable as the final truth for whether a candidate is strict or should be selected.

## Requirements

- Reuse Playwright upstream selector logic as much as practical.
- Stop using the custom uniqueness matcher as the final authority.
- Preserve the existing recorder payload shape so configure/export/generator flows keep working.
- Keep the fix scoped to recorder locator generation and validation, not a larger RPA engine rewrite.

## Chosen Approach

Vendor a minimal Playwright injected selector subset into the backend RPA module and use it from the recorder injection script.

The vendored subset will provide:

- Upstream role semantics from `roleUtils`
- Upstream text extraction semantics from `selectorUtils`
- Upstream candidate generation and scoring from `selectorGenerator`

The recorder injection script will:

- call the vendored selector generator to obtain the primary selector and alternative selectors
- translate Playwright selector expressions into the existing JSON locator format
- derive `locator_candidates`, `strict_match_count`, `selected`, and `validation` from Playwright selector results rather than recorder-local approximations

## Non-Goals

- Replacing the Python generator locator JSON model
- Reworking the configure page UX
- Syncing every Playwright injected utility into the repository

## Implementation Outline

1. Add a vendored JS bundle under `backend/rpa/vendor/` containing the minimal upstream selector dependencies plus a thin adapter.
2. Replace the custom selector generation path in `CAPTURE_JS` with calls into the vendored adapter.
3. Keep Python-side locator parsing and candidate promotion, but feed it Playwright-backed candidate metadata.
4. Add regression tests for:
   - anchor without `href` does not produce a selected `role=link`
   - text candidate is promoted when it is the Playwright-strict match

## Risks

- The vendored bundle adds maintenance burden when upgrading Playwright.
- Selector expression parsing must remain compatible with the subset emitted by the vendored adapter.

## Mitigations

- Keep the vendor scope intentionally small and documented.
- Add tests around emitted locator expressions and selected candidate normalization.

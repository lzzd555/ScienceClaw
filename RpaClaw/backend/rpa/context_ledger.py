"""Task-scoped context ledger for RPA skill recording.

Tracks observed values, derived values, and rebuild actions so that the
skill generator can decide which runtime context must be preserved across
playback steps.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextValue(BaseModel):
    """A single observed or derived value within a recording session."""

    key: str
    value: Any = None
    user_explicit: bool = False
    runtime_required: bool = False
    source_step_id: str | None = None
    source_kind: str = "observation"


class ContextRebuildAction(BaseModel):
    """An action needed to reconstruct context during playback."""

    action: str
    description: str
    step_ref: str | None = None
    writes: list[str] = Field(default_factory=list)


class TaskContextLedger(BaseModel):
    """Aggregates all context information for a single RPA recording session.

    The ledger tracks three categories of data:

    - *page_context*: free-form dict describing the current page state (URL,
      title, etc.).
    - *observed_values*: values captured from the DOM or page during recording.
    - *derived_values*: values computed or inferred by the assistant.
    - *rebuild_actions*: actions that must be replayed to re-establish context
      during playback.

    The ``should_promote_value`` method decides whether a value is important
    enough to persist into the generated skill script.
    """

    page_context: dict[str, Any] = Field(default_factory=dict)
    observed_values: dict[str, ContextValue] = Field(default_factory=dict)
    derived_values: dict[str, ContextValue] = Field(default_factory=dict)
    rebuild_actions: list[ContextRebuildAction] = Field(default_factory=list)

    # ── Promotion rules ────────────────────────────────────────────────

    def should_promote_value(
        self,
        key: str,
        source: str,
        user_explicit: bool,
        runtime_required: bool,
        consumed_later: bool,
    ) -> bool:
        """Decide whether *key* should be promoted into the generated script.

        Promotion rules (evaluated in order):

        1. **Must promote** when ``user_explicit=True`` — the user explicitly
           requested extraction of this value.
        2. **Must promote** when ``runtime_required=True`` *and*
           ``consumed_later=True`` — the value is a cross-page dependency
           (e.g. a CSRF token) needed later during playback.
        3. **Must NOT promote** transient observations where ``source`` is
           ``"observation"`` and all flags are ``False``.
        4. **Default**: do not promote.
        """
        # Rule 1: user explicitly requested → always promote
        if user_explicit:
            return True

        # Rule 2: runtime dependency consumed in a later step → promote
        if runtime_required and consumed_later:
            return True

        # Rule 3: transient observation with no flags → never promote
        if source == "observation" and not user_explicit and not runtime_required:
            return False

        # Rule 4: conservative default — do not promote
        return False

    # ── Recording helpers ──────────────────────────────────────────────

    def record_value(
        self,
        category: str,
        key: str,
        value: Any,
        *,
        user_explicit: bool = False,
        runtime_required: bool = False,
        source_step_id: str | None = None,
        source_kind: str = "observation",
    ) -> None:
        """Record a context value into *observed_values* or *derived_values*.

        Parameters
        ----------
        category:
            Either ``"observed"`` or ``"derived"``.  Determines which dict
            the value is stored in.
        key:
            Unique identifier for the value within its category.
        value:
            The actual value to store.
        user_explicit:
            Whether the user explicitly asked for this extraction.
        runtime_required:
            Whether playback depends on this value.
        source_step_id:
            ID of the RPA step that produced this value.
        source_kind:
            Origin of the value (e.g. ``"observation"``, ``"dom_extraction"``).
        """
        entry = ContextValue(
            key=key,
            value=value,
            user_explicit=user_explicit,
            runtime_required=runtime_required,
            source_step_id=source_step_id,
            source_kind=source_kind,
        )
        if category == "observed":
            self.observed_values[key] = entry
        elif category == "derived":
            self.derived_values[key] = entry
        else:
            raise ValueError(f"Unknown context value category: {category!r}")

    def record_rebuild_action(
        self,
        action: str,
        description: str,
        *,
        step_ref: str | None = None,
        writes: list[str] | None = None,
    ) -> None:
        """Append a rebuild action to the ledger.

        Parameters
        ----------
        action:
            Short machine-readable identifier (e.g. ``"navigate"``,
            ``"extract_table"``).
        description:
            Human-readable explanation of the action.
        step_ref:
            Optional ID of the RPA step this action is associated with.
        writes:
            List of context keys this action produces.
        """
        self.rebuild_actions.append(
            ContextRebuildAction(
                action=action,
                description=description,
                step_ref=step_ref,
                writes=writes or [],
            )
        )

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from backend.rpa.context_ledger import TaskContextLedger


_LEGACY_CONTEXT_READ_RE = re.compile(r"context:([A-Za-z_][A-Za-z0-9_]*)")
_LEGACY_CONTEXT_READ_EXACT_RE = re.compile(r"^context:([A-Za-z_][A-Za-z0-9_]*)$")
_ALL_CONTEXT_QUERY_HINTS = ("所有内容", "全部", "有哪些")
_CONTEXT_QUERY_HINTS = ("上下文", "context", "记录", "保存", "当前", "现在")
_STEP_CONTRACT_LEGACY_FIELDS = ("value", "prompt", "description", "target")


@dataclass(slots=True)
class StepContextContract:
    """Minimal contract describing the context a step needs."""

    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    updates: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reads = self._dedupe(self.reads)
        self.writes = self._dedupe(self.writes or self.updates.keys())

    @staticmethod
    def _dedupe(values: Iterable[Any]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped


class SessionContextService:
    def __init__(self, ledger: TaskContextLedger):
        self.ledger = ledger

    def export_generator_contract(
        self,
        steps: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_steps: list[dict[str, Any]] = []
        required_outputs: set[str] = set()
        rebuild_plan: list[int] = []

        for step_index, original_step in enumerate(steps or []):
            step = dict(original_step)
            reads = self.collect_step_contract_reads(
                declared_reads=step.get("context_reads") or [],
                step_data=step,
            )
            writes = StepContextContract(writes=step.get("context_writes") or []).writes
            step["context_reads"] = reads
            step["context_writes"] = writes
            step["context_contract"] = {
                "reads": reads,
                "writes": writes,
            }
            normalized_steps.append(step)
            if writes:
                required_outputs.update(writes)
                rebuild_plan.append(step_index)

        return {
            "steps": normalized_steps,
            "required_context_outputs": sorted(required_outputs),
            "context_rebuild_plan": rebuild_plan,
            "rebuild_sequence": self._export_rebuild_sequence(),
        }

    def build_current_context(self) -> dict[str, Any]:
        build_value_map = getattr(self.ledger, "build_value_map", None)
        if callable(build_value_map):
            return build_value_map()

        value_map: dict[str, Any] = {}
        for collection_name in ("observed_values", "derived_values"):
            collection = getattr(self.ledger, collection_name, {}) or {}
            for key, entry in collection.items():
                value = getattr(entry, "value", entry)
                value_map[key] = value
        return value_map

    def record_updates(
        self,
        updates: dict[str, Any],
        *,
        category: str = "observed",
        user_explicit: bool = False,
        runtime_required: bool = False,
        source_step_id: str | None = None,
        source_kind: str = "observation",
    ) -> list[str]:
        written_keys: list[str] = []
        record_value = getattr(self.ledger, "record_value", None)
        for key, value in updates.items():
            if callable(record_value):
                record_value(
                    category=category,
                    key=key,
                    value=value,
                    user_explicit=user_explicit,
                    runtime_required=runtime_required,
                    source_step_id=source_step_id,
                    source_kind=source_kind,
                )
            else:
                collection_name = "observed_values" if category == "observed" else "derived_values"
                collection = getattr(self.ledger, collection_name, None)
                if isinstance(collection, dict):
                    collection[key] = value
            written_keys.append(key)
        return written_keys

    def capture_runtime_contract(
        self,
        *,
        before_context: dict[str, Any] | None = None,
        after_context: dict[str, Any] | None = None,
        declared_reads: Iterable[str] | None = None,
        legacy_text: str | None = None,
    ) -> StepContextContract:
        before = before_context or {}
        after = after_context or {}
        updates = {
            key: value
            for key, value in after.items()
            if key not in before or before.get(key) != value
        }
        return StepContextContract(
            reads=self.collect_declared_reads(declared_reads, legacy_text=legacy_text),
            updates=updates,
        )

    def apply_contract_writes(
        self,
        contract: StepContextContract,
        *,
        category: str = "observed",
        user_explicit: bool = False,
        runtime_required: bool = False,
        source_step_id: str | None = None,
        source_kind: str = "observation",
    ) -> list[str]:
        payload = self._build_write_payload(contract)
        if not payload:
            return []
        return self.record_updates(
            payload,
            category=category,
            user_explicit=user_explicit,
            runtime_required=runtime_required,
            source_step_id=source_step_id,
            source_kind=source_kind,
        )

    def collect_step_contract_reads(
        self,
        *,
        declared_reads: Iterable[str] | None = None,
        step_data: dict[str, Any] | None = None,
    ) -> list[str]:
        explicit_reads = self.collect_declared_reads(declared_reads)
        if explicit_reads:
            return explicit_reads

        legacy_parts: list[str] = []
        for field in _STEP_CONTRACT_LEGACY_FIELDS:
            value = (step_data or {}).get(field)
            if isinstance(value, str) and value.strip():
                legacy_parts.append(value)

        legacy_text = "\n".join(legacy_parts) if legacy_parts else None
        return self.collect_declared_reads(
            declared_reads,
            legacy_text=legacy_text,
        )

    def answer_context_query(self, query: str) -> dict[str, Any]:
        context = self.build_current_context()
        if self._is_all_context_query(query):
            return self._build_answer_payload("all", context, query)

        declared_reads = self.collect_context_query_reads(query)
        values = {read: context[read] for read in declared_reads if read in context}
        if values:
            return self._build_answer_payload("keys" if len(values) > 1 else "key", values, query)

        if query in context:
            return self._build_answer_payload("key", {query: context[query]}, query)

        return self._build_answer_payload("missing", {}, query)

    def maybe_answer_context_query(self, query: str) -> dict[str, Any] | None:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return None

        context = self.build_current_context()
        if normalized_query in context:
            return self.answer_context_query(normalized_query)

        reads = self.collect_context_query_reads(normalized_query)
        if reads:
            return self.answer_context_query(normalized_query)

        if self._is_all_context_query(normalized_query):
            return self.answer_context_query(normalized_query)

        return None

    def collect_declared_reads(
        self,
        declared_reads: Iterable[str] | None = None,
        *,
        legacy_text: str | None = None,
    ) -> list[str]:
        reads = [self._normalize_declared_read(item) for item in declared_reads or []]
        if legacy_text:
            reads.extend(_LEGACY_CONTEXT_READ_RE.findall(legacy_text))

        deduped: list[str] = []
        seen: set[str] = set()
        for read in reads:
            if read and read not in seen:
                seen.add(read)
                deduped.append(read)
        return deduped

    def collect_context_query_reads(self, query: str) -> list[str]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        reads = self.collect_declared_reads(legacy_text=normalized_query)
        context = self.build_current_context()
        for key in context:
            if self._query_mentions_key(normalized_query, key):
                reads.append(key)
        return self.collect_declared_reads(reads)

    def _is_all_context_query(self, query: str) -> bool:
        return any(hint in query for hint in _ALL_CONTEXT_QUERY_HINTS) and any(
            marker in query for marker in _CONTEXT_QUERY_HINTS
        )

    def _query_mentions_key(self, query: str, key: str) -> bool:
        if key == query:
            return True
        if key not in query:
            return False
        return any(marker in query for marker in _CONTEXT_QUERY_HINTS) or "?" in query or "？" in query or "是什么" in query

    def _normalize_declared_read(self, read: Any) -> str:
        normalized = str(read).strip()
        match = _LEGACY_CONTEXT_READ_EXACT_RE.match(normalized)
        if match:
            return match.group(1)
        return normalized

    def _build_write_payload(self, contract: StepContextContract) -> dict[str, Any]:
        if not contract.writes:
            return {}

        payload: dict[str, Any] = {}
        for key in contract.writes:
            if key not in contract.updates:
                raise ValueError(f"Missing update payload for declared write: {key}")
            payload[key] = contract.updates[key]
        return payload

    def _export_rebuild_sequence(self) -> list[dict[str, Any]]:
        sequence: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for action in getattr(self.ledger, "rebuild_actions", []) or []:
            writes = StepContextContract(writes=getattr(action, "writes", []) or []).writes
            entry: dict[str, Any] = {
                "action": getattr(action, "action", ""),
                "description": getattr(action, "description", ""),
                "writes": writes,
                "source_step_id": getattr(action, "step_ref", None),
            }
            if entry["action"] == "navigate":
                entry["url"] = entry["description"]
            sequence.append(entry)
            seen_keys.update(writes)

        for key, entry in (getattr(self.ledger, "observed_values", {}) or {}).items():
            if key in seen_keys or not getattr(entry, "user_explicit", False):
                continue
            sequence.append(
                {
                    "action": "observe",
                    "description": f"Observed value: {key}",
                    "writes": [key],
                    "source_step_id": getattr(entry, "source_step_id", None),
                    "value": getattr(entry, "value", entry),
                }
            )
            seen_keys.add(key)

        for key, entry in (getattr(self.ledger, "derived_values", {}) or {}).items():
            if key in seen_keys or not getattr(entry, "runtime_required", False):
                continue
            sequence.append(
                {
                    "action": "derive",
                    "description": f"Derived value: {key}",
                    "writes": [key],
                    "source_step_id": getattr(entry, "source_step_id", None),
                    "value": getattr(entry, "value", entry),
                }
            )
            seen_keys.add(key)

        return sequence

    def _build_answer_payload(self, mode: str, values: dict[str, Any], query: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "values": values,
            "text": self._render_context_text(values, query=query),
        }

    def _render_context_text(self, values: dict[str, Any], *, query: str | None = None) -> str:
        if not values:
            if query:
                return f"未找到与“{query}”匹配的上下文值。"
            return "当前没有可用的上下文值。"

        lines = [f"{key}: {value}" for key, value in values.items()]
        return "\n".join(lines)

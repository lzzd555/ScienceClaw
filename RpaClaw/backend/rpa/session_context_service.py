from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from backend.rpa.context_ledger import TaskContextLedger


_LEGACY_CONTEXT_READ_RE = re.compile(r"context:([A-Za-z_][A-Za-z0-9_]*)")
_ALL_CONTEXT_QUERY_HINTS = ("所有内容", "全部", "有哪些")


@dataclass(slots=True)
class StepContextContract:
    """Minimal contract describing the context a step needs."""

    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    updates: dict[str, Any] = field(default_factory=dict)


class SessionContextService:
    def __init__(self, ledger: TaskContextLedger):
        self.ledger = ledger

    def build_current_context(self) -> dict[str, Any]:
        return self.ledger.build_value_map()

    def answer_context_query(self, query: str) -> dict[str, Any]:
        context = self.build_current_context()
        if self._is_all_context_query(query):
            return self._build_answer_payload("all", context, query)

        declared_reads = self.collect_declared_reads(legacy_text=query)
        for read in declared_reads:
            if read in context:
                return self._build_answer_payload("key", {read: context[read]}, query)

        if query in context:
            return self._build_answer_payload("key", {query: context[query]}, query)

        return self._build_answer_payload("missing", {}, query)

    def collect_declared_reads(
        self,
        declared_reads: Iterable[str] | None = None,
        *,
        legacy_text: str | None = None,
    ) -> list[str]:
        reads = [str(item) for item in declared_reads or []]
        if legacy_text:
            reads.extend(_LEGACY_CONTEXT_READ_RE.findall(legacy_text))

        deduped: list[str] = []
        seen: set[str] = set()
        for read in reads:
            if read and read not in seen:
                seen.add(read)
                deduped.append(read)
        return deduped

    def _is_all_context_query(self, query: str) -> bool:
        return "上下文" in query and any(hint in query for hint in _ALL_CONTEXT_QUERY_HINTS)

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

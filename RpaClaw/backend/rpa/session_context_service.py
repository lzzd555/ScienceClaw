from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from backend.rpa.context_ledger import TaskContextLedger


_LEGACY_CONTEXT_READ_RE = re.compile(r"context:([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(slots=True)
class StepContextContract:
    """Minimal contract describing the context a step needs."""

    declared_reads: list[str] = field(default_factory=list)
    prompt: str | None = None


class SessionContextService:
    def __init__(self, ledger: TaskContextLedger):
        self.ledger = ledger

    def build_current_context(self) -> dict[str, Any]:
        return self.ledger.build_value_map()

    def answer_context_query(self, query: str) -> Any:
        context = self.build_current_context()
        if query in context:
            return context[query]

        declared_reads = self.collect_declared_reads(query)
        if declared_reads:
            return context.get(declared_reads[0])

        return None

    def collect_declared_reads(self, source: StepContextContract | str | Iterable[str] | None) -> list[str]:
        reads: list[str] = []
        if source is None:
            return reads

        if isinstance(source, StepContextContract):
            reads.extend(source.declared_reads)
            if source.prompt:
                reads.extend(_LEGACY_CONTEXT_READ_RE.findall(source.prompt))
        elif isinstance(source, str):
            reads.extend(_LEGACY_CONTEXT_READ_RE.findall(source))
        else:
            reads.extend(str(item) for item in source)

        deduped: list[str] = []
        seen: set[str] = set()
        for read in reads:
            if read and read not in seen:
                seen.add(read)
                deduped.append(read)
        return deduped

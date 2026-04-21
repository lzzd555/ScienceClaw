from __future__ import annotations

from importlib import import_module

from .context_ledger import ContextRebuildAction, ContextValue, TaskContextLedger

__all__ = [
    "TaskContextLedger",
    "ContextValue",
    "ContextRebuildAction",
    "rpa_manager",
    "RPASession",
    "RPAStep",
    "cdp_connector",
]


def __getattr__(name: str):
    if name in {"rpa_manager", "RPASession", "RPAStep"}:
        module = import_module(".manager", __name__)
        return getattr(module, name)
    if name == "cdp_connector":
        module = import_module(".cdp_connector", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

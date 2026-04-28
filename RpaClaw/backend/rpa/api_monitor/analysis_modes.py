"""Analysis mode registry for API Monitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AnalysisHandler = Literal["free", "directed"]
AnalysisBusinessSafety = Literal["none", "guarded", "user_controlled"]


@dataclass(frozen=True)
class AnalysisModeConfig:
    key: str
    label: str
    handler: AnalysisHandler
    requires_instruction: bool
    business_safety: AnalysisBusinessSafety = "none"


ANALYSIS_MODE_REGISTRY: dict[str, AnalysisModeConfig] = {
    "free": AnalysisModeConfig(
        key="free",
        label="自由分析",
        handler="free",
        requires_instruction=False,
        business_safety="none",
    ),
    "safe_directed": AnalysisModeConfig(
        key="safe_directed",
        label="安全分析",
        handler="directed",
        requires_instruction=True,
        business_safety="guarded",
    ),
    "directed": AnalysisModeConfig(
        key="directed",
        label="定向分析",
        handler="directed",
        requires_instruction=True,
        business_safety="user_controlled",
    ),
}


def normalize_analysis_mode(value: str | None) -> str:
    mode = str(value or "free").strip() or "free"
    return mode


def get_analysis_mode_config(value: str | None) -> AnalysisModeConfig:
    mode = normalize_analysis_mode(value)
    config = ANALYSIS_MODE_REGISTRY.get(mode)
    if config is None:
        raise ValueError(f"Unknown API Monitor analysis mode: {mode}")
    return config

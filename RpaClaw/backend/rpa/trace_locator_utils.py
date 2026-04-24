from __future__ import annotations

from typing import Any, Dict, List, Optional


_VALUE_METHODS = {"text", "testid", "label", "placeholder", "alt", "title", "css"}


def normalize_locator(locator: Any) -> Dict[str, Any]:
    if not isinstance(locator, dict):
        return {}

    method = str(locator.get("method") or "").strip()
    if not method and locator.get("role"):
        method = "role"
    if not method and isinstance(locator.get("value"), str) and locator.get("value").strip():
        method = "css"
    if not method:
        return {}

    normalized: Dict[str, Any] = {"method": method}

    if method == "role":
        role = str(locator.get("role") or "").strip()
        if not role:
            return {}
        normalized["role"] = role
        if isinstance(locator.get("name"), str) and locator.get("name").strip():
            normalized["name"] = locator["name"].strip()
        if locator.get("exact") is not None:
            normalized["exact"] = bool(locator.get("exact"))
        return normalized

    if method in _VALUE_METHODS:
        value = str(locator.get("value") or "").strip()
        if not value:
            return {}
        normalized["value"] = value
        if locator.get("exact") is not None:
            normalized["exact"] = bool(locator.get("exact"))
        return normalized

    if method == "nested":
        parent = normalize_locator(locator.get("parent"))
        child = normalize_locator(locator.get("child"))
        if not parent or not child:
            return {}
        normalized["parent"] = parent
        normalized["child"] = child
        return normalized

    if method == "nth":
        base = normalize_locator(locator.get("locator") or locator.get("base"))
        if not base:
            return {}
        try:
            index = int(locator.get("index") or 0)
        except Exception:
            return {}
        normalized["locator"] = base
        normalized["index"] = max(index, 0)
        return normalized

    return {}


def normalize_locator_candidates(
    candidates: Any,
    *,
    target: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    normalized_candidates: List[Dict[str, Any]] = []
    target = normalize_locator(target or {})
    target_key = repr(target) if target else ""

    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            locator = normalize_locator(candidate.get("locator") if "locator" in candidate else candidate)
            if not locator:
                continue
            item = dict(candidate)
            item["locator"] = locator
            normalized_candidates.append(item)

    selected_index = next(
        (index for index, candidate in enumerate(normalized_candidates) if candidate.get("selected")),
        None,
    )
    if selected_index is None and target_key:
        selected_index = next(
            (
                index
                for index, candidate in enumerate(normalized_candidates)
                if repr(candidate.get("locator")) == target_key
            ),
            None,
        )

    if normalized_candidates and target:
        if selected_index is None:
            selected_index = 0
        normalized_candidates[selected_index]["locator"] = target
    elif selected_index is None and target:
        normalized_candidates.insert(0, {"locator": target, "selected": True})
        selected_index = 0

    if normalized_candidates and selected_index is None:
        selected_index = 0

    for index, candidate in enumerate(normalized_candidates):
        candidate["selected"] = index == selected_index

    return normalized_candidates


def has_valid_locator(locator: Any) -> bool:
    return bool(normalize_locator(locator))

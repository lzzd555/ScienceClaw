from __future__ import annotations

from typing import Any, Dict, List, Optional


COMPLEX_CONTAINER_KINDS = {"table", "grid", "list", "tree", "card_group", "toolbar", "form_section"}
RELATIVE_TOKENS = ("第一个", "最后一个", "这行", "右边", "左边", "first", "last")


def has_relative_terms(intent: Dict[str, Any]) -> bool:
    prompt = " ".join(str(intent.get(key) or "") for key in ("prompt", "description")).lower()
    return any(token in prompt for token in RELATIVE_TOKENS)


def containers_by_id(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(container.get("container_id") or ""): container
        for container in snapshot.get("containers", [])
        if container.get("container_id")
    }


def pick_expansion_container(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not has_relative_terms(intent):
        return None

    actionable_nodes = list(snapshot.get("actionable_nodes") or [])
    if len(actionable_nodes) < 2:
        return None

    indexed_containers = containers_by_id(snapshot)
    for node in actionable_nodes:
        container_id = str(node.get("container_id") or "")
        container = indexed_containers.get(container_id)
        if not container:
            continue
        if str(container.get("container_kind") or "") not in COMPLEX_CONTAINER_KINDS:
            continue
        child_ids = list(container.get("child_actionable_ids") or [])
        if len(child_ids) >= 2:
            return container
    return None

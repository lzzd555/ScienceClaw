"""LLM integration for API Monitor.

Two prompts:
1. DOM element safety analysis - classify interactive elements as safe/skip
2. API call -> YAML tool definition generation
"""

import json
import logging
import re
from typing import AsyncGenerator, Dict, List, Optional

from backend.deepagent.engine import get_llm_model

from .models import CapturedApiCall

logger = logging.getLogger(__name__)

# ── Element analysis prompt ──────────────────────────────────────────

ELEMENT_ANALYSIS_SYSTEM = """\
You are a web automation safety analyzer. Given a list of interactive elements on a web page, \
classify each one as either "safe_to_probe" or "skip".

Rules for "skip":
- Elements with text containing: delete, remove, logout, sign out, sign out, cancel subscription, \
  reset, purge, drop, uninstall, deactivate, disable, revoke, eject, reject, decline, block, ban
- Elements that navigate to a different domain (external links)
- Elements that trigger file downloads
- Form submit buttons on payment/checkout forms
- Elements with role="destructive"

Rules for "safe_to_probe":
- Navigation within the same site
- Search buttons, filter buttons, pagination
- Tab switches, accordion toggles
- Form inputs (text, select, checkbox)
- Dialog/modal open buttons
- "Load more" / "Show more" buttons
- Table row clicks, list item clicks

Return a JSON object with keys "safe" and "skip", each containing a list of element indices (0-based).
Only return valid JSON, no markdown fences.
"""

ELEMENT_ANALYSIS_USER = """\
Page URL: {url}

Interactive elements:
{elements_json}

Classify each element. Return JSON: {{"safe": [0, 2, 5, ...], "skip": [1, 3, 4, ...]}}
"""

# ── Tool generation prompt ───────────────────────────────────────────

TOOL_GEN_SYSTEM = """\
You are an API tool definition generator. Given HTTP API call samples captured from a web application, \
generate an OpenAI function calling format tool definition in YAML.

The YAML must have this structure:
```yaml
name: <snake_case_function_name>
description: <clear description of what this API endpoint does>
method: <HTTP method>
url: <parameterized URL path>
parameters:
  type: object
  properties:
    <param_name>:
      type: <string|integer|boolean|array|object>
      description: <what this parameter does>
      in: <query|path|body|header>
  required:
    - <required_param_names>
response:
  type: object
  properties:
    <field_name>:
      type: <type>
      description: <what this field contains>
```

Guidelines:
- Function names should be descriptive snake_case (e.g., list_users, create_order, search_products)
- Parameterize URL path segments that look like IDs: /users/123 -> /users/{user_id}
- Include all visible query parameters and request body fields
- Mark parameters as required only if they appear in every sample or seem essential
- Infer response schema from the captured response bodies
- Only return valid YAML, no markdown fences, no extra commentary
"""

TOOL_GEN_USER = """\
Endpoint: {method} {url_pattern}
Page context: {page_context}

API call samples:
{samples_json}

Generate the YAML tool definition.
"""

# ── LLM call helpers ─────────────────────────────────────────────────


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    model_config: Optional[Dict] = None,
) -> str:
    """Call LLM with system + user messages and return full text response."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    response = await model.ainvoke(messages)
    text = ""
    if isinstance(response, AIMessage):
        text = response.content or ""
    elif hasattr(response, "content"):
        text = str(response.content)
    else:
        text = str(response)
    return text.strip()


# ── Public API ───────────────────────────────────────────────────────


async def analyze_elements(
    url: str,
    elements: List[Dict],
    model_config: Optional[Dict] = None,
) -> Dict[str, List[int]]:
    """Classify interactive elements as safe or skip.

    Returns {"safe": [indices], "skip": [indices]}.
    """
    if not elements:
        return {"safe": [], "skip": []}

    user_prompt = ELEMENT_ANALYSIS_USER.format(
        url=url,
        elements_json=json.dumps(elements, indent=2, ensure_ascii=False),
    )

    raw = await _call_llm(ELEMENT_ANALYSIS_SYSTEM, user_prompt, model_config)

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        result = json.loads(raw)
        return {
            "safe": result.get("safe", []),
            "skip": result.get("skip", []),
        }
    except json.JSONDecodeError:
        logger.warning("[ApiMonitor] Failed to parse element analysis response: %s", raw[:200])
        return {"safe": list(range(len(elements))), "skip": []}


async def generate_tool_definition(
    method: str,
    url_pattern: str,
    samples: List[CapturedApiCall],
    page_context: str = "",
    model_config: Optional[Dict] = None,
) -> str:
    """Generate an OpenAI YAML tool definition from captured API call samples.

    Returns the raw YAML string.
    """
    sample_data = []
    for call in samples[:5]:
        entry: Dict = {
            "request_body": None,
            "response_status": None,
            "response_body": None,
        }
        if call.request.body:
            try:
                entry["request_body"] = json.loads(call.request.body)
            except (json.JSONDecodeError, TypeError):
                entry["request_body"] = call.request.body
        if call.response:
            entry["response_status"] = call.response.status
            if call.response.body:
                try:
                    entry["response_body"] = json.loads(call.response.body)
                except (json.JSONDecodeError, TypeError):
                    entry["response_body"] = call.response.body
        sample_data.append(entry)

    user_prompt = TOOL_GEN_USER.format(
        method=method,
        url_pattern=url_pattern,
        page_context=page_context or "Unknown page",
        samples_json=json.dumps(sample_data, indent=2, ensure_ascii=False),
    )

    raw = await _call_llm(TOOL_GEN_SYSTEM, user_prompt, model_config)

    raw = re.sub(r"^```(?:yaml)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    return raw.strip()

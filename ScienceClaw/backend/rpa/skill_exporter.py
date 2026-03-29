import logging
import os
import json
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Same directory used by the skills API in route/sessions.py
_EXTERNAL_SKILLS_DIR = os.environ.get("EXTERNAL_SKILLS_DIR", "/app/Skills")

class SkillExporter:
    """Export recorded skills to MCP format"""

    def __init__(self, workspace_path: str = None):
        self.workspace_path = workspace_path or _EXTERNAL_SKILLS_DIR
        os.makedirs(self.workspace_path, exist_ok=True)

    def export_skill(
        self,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any]
    ) -> str:
        """Export skill as MCP tool"""

        skill_dir = os.path.join(self.workspace_path, skill_name)
        os.makedirs(skill_dir, exist_ok=True)

        # Generate input schema
        input_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }

        for param_name, param_info in params.items():
            input_schema["properties"][param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", "")
            }
            if param_info.get("required", False):
                input_schema["required"].append(param_name)

        # Create SKILL.md with YAML front-matter (required by skills API)
        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

See `skill.py` for the Playwright implementation.
"""

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(skill_md)

        # Save script
        script_path = os.path.join(skill_dir, "skill.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        logger.info(f"Skill exported to {skill_dir}")
        return skill_dir

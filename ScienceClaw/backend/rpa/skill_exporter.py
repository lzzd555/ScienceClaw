import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

from backend.storage import get_repository
from backend.config import settings

logger = logging.getLogger(__name__)


class SkillExporter:
    """Export recorded RPA skills to MongoDB or local filesystem."""

    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
    ) -> str:
        """Export skill to MongoDB or local filesystem based on storage_backend.

        Returns the skill name on success.
        """
        # Generate input schema
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        for param_name, param_info in params.items():
            input_schema["properties"][param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", ""),
            }
            if param_info.get("required", False):
                input_schema["required"].append(param_name)

        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Usage

To execute this skill, run:

```bash
python3 skill.py
```

The skill uses Playwright to automate browser interactions based on the recorded steps.

## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

The skill is implemented in `skill.py` using Playwright for browser automation.
"""

        if settings.storage_backend == "local":
            # Save to filesystem
            skill_dir = Path(settings.external_skills_dir) / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)

            (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
            (skill_dir / "skill.py").write_text(script, encoding="utf-8")
            # Save params config (includes credential_id for sensitive params)
            (skill_dir / "params.json").write_text(
                json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            logger.info(f"Skill '{skill_name}' exported to {skill_dir}")
        else:
            # Save to MongoDB
            now = datetime.now(timezone.utc)
            col = get_repository("skills")
            await col.update_one(
                {"user_id": user_id, "name": skill_name},
                {
                    "$set": {
                        "files": {
                            "SKILL.md": skill_md,
                            "skill.py": script,
                        },
                        "description": description,
                        "params": params,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "name": skill_name,
                        "source": "rpa",
                        "blocked": False,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            logger.info(f"Skill '{skill_name}' exported to MongoDB for user {user_id}")

        return skill_name

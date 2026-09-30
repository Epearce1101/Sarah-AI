# backend/ai_v11/tools_engine.py
from typing import Dict, Any, List
from backend.models_sql import get_all_skills, set_skill_enabled, register_skill


class ToolsEngine:
    """
    Wrapper around the SQL 'skills' table.
    Later you can extend this to actually install pip/npm/system tools.
    """

    def list_skills(self) -> List[Dict[str, Any]]:
        return get_all_skills()

    def enable_skill(self, slug: str):
        set_skill_enabled(slug, True)

    def disable_skill(self, slug: str):
        set_skill_enabled(slug, False)

    def register_skill(
        self,
        name: str,
        slug: str,
        description: str = "",
        enabled: bool = True,
        config_json: str = "{}",
    ):
        register_skill(
            name=name,
            slug=slug,
            description=description,
            enabled=enabled,
            config_json=config_json,
        )

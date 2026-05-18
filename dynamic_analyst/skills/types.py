from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    version: str
    description: str
    intent_tags: list[str] = field(default_factory=list)
    domain: str = ""
    required_inputs: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    safety_checks: list[str] = field(default_factory=list)
    fallback: str = "generic_react"

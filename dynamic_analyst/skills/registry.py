from __future__ import annotations

from dataclasses import asdict

from .builtin import list_builtin_specs
from .types import SkillSpec


class SkillRegistry:
    def __init__(self, specs: list[SkillSpec] | None = None):
        base = specs or list_builtin_specs()
        self._by_id: dict[str, SkillSpec] = {}
        for spec in base:
            sid = str(spec.skill_id or "").strip()
            if not sid:
                continue
            self._by_id[sid] = spec

    def list(self) -> list[SkillSpec]:
        return [self._by_id[sid] for sid in sorted(self._by_id)]

    def get(self, skill_id: str) -> SkillSpec | None:
        return self._by_id.get(str(skill_id or "").strip())

    def as_dicts(self) -> list[dict]:
        return [asdict(spec) for spec in self.list()]


_REGISTRY: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SkillRegistry()
    return _REGISTRY

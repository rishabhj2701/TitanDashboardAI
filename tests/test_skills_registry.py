from dynamic_analyst.skills.registry import get_skill_registry


def test_registry_has_builtin_skills():
    registry = get_skill_registry()
    ids = [spec.skill_id for spec in registry.list()]
    assert "road_corridor_map_scope" in ids
    assert "crash_near_hard_brake_conflation" in ids
    assert "crash_map_overview" in ids
    assert "roads_speeding_over_limit" in ids
    assert "roads_density_filter" in ids

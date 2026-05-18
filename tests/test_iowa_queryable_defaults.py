from dynamic_analyst.demo_queryable_defaults import merge_iowa_queryable_fields


def test_merge_iowa_crash_includes_routeid():
    merged = merge_iowa_queryable_fields("crash", {"fields": [{"query_name": "severity", "enabled": True}]})
    names = {f["query_name"] for f in merged["fields"]}
    assert "routeid" in names
    assert "severity" in names


def test_merge_iowa_cv_includes_hour_and_start_ts():
    merged = merge_iowa_queryable_fields("cv", {})
    names = {f["query_name"] for f in merged["fields"]}
    assert "hour" in names
    assert "start_ts" in names
    assert "route_id" in names

from dynamic_analyst.sql.filters import _collect_road_filter_scope_values


def test_collect_road_filter_scope_values_strips_wildcards_for_search_ops():
    exact: list[str] = []
    search: list[str] = []
    _collect_road_filter_scope_values(
        [
            {"column": "road_name", "operator": "ilike", "value": "%I 70%"},
            {"column": "road_name", "operator": "contains", "value": '"I-70%"'},
        ],
        exact,
        search,
    )

    assert exact == []
    assert search == ["I 70", "I-70"]


def test_collect_road_filter_scope_values_keeps_exact_values():
    exact: list[str] = []
    search: list[str] = []
    _collect_road_filter_scope_values(
        [
            {"column": "road_name", "operator": "=", "value": " I-70 "},
            {"column": "road_name", "operator": "in", "value": [" I-44 ", "US-40"]},
        ],
        exact,
        search,
    )

    assert exact == ["I-70", "I-44", "US-40"]
    assert search == []

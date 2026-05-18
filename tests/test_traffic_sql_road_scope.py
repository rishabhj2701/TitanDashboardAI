from dynamic_analyst.sql import traffic


def test_cv_enrichment_joins_cast_road_keys_to_text(monkeypatch):
    from dynamic_analyst.sql import catalog

    def _fake_first_existing_relation(conn, candidates):
        if any("osm_roads_match" in candidate for candidate in candidates):
            return "public.osm_roads_match"
        if any("cv_road_stats_mv" in candidate for candidate in candidates):
            return "public.cv_road_stats_mv"
        return None

    def _fake_table_column_names(conn, table_name):
        if table_name == "public.cv_points":
            return {"way_id", "speed", "dataset_id", "attrs"}
        if table_name == "public.osm_roads_match":
            return {"way_id", "name", "ref", "highway"}
        if table_name == "public.cv_road_stats_mv":
            return {"way_id", "label", "name", "ref", "speed_limit_mph"}
        return set()

    monkeypatch.setattr(catalog, "_first_existing_relation", _fake_first_existing_relation)
    monkeypatch.setattr(catalog, "_cv_relation_candidates", lambda conn, rel: [f"public.{rel}"])
    monkeypatch.setattr(catalog, "_table_column_names", _fake_table_column_names)

    enrichment = catalog._resolve_cv_enrichment_sql(
        object(),
        "public.cv_points",
        alias="p",
        prefer_route_ref=True,
    )

    joined_sql = " ".join(enrichment["joins"])
    assert "orm.way_id = p.way_id" not in joined_sql
    assert "rs.way_id = p.way_id" not in joined_sql
    assert "NULLIF(TRIM((orm.way_id)::text), '') = NULLIF(TRIM((p.way_id)::text), '')" in joined_sql
    assert "NULLIF(TRIM((rs.way_id)::text), '') = NULLIF(TRIM((p.way_id)::text), '')" in joined_sql


def test_road_only_map_with_head_short_circuits_before_db(monkeypatch):
    saved = {}

    monkeypatch.setattr(traffic, "_latest_cv_dataset_id", lambda: "cv_ds_latest")
    monkeypatch.setattr(traffic, "get_active_session", lambda: None)
    monkeypatch.setattr(traffic, "get_last_map", lambda _sid: None)

    def _save_map(payload, map_type="traffic"):
        saved["payload"] = payload
        saved["map_type"] = map_type

    monkeypatch.setattr(traffic, "save_map_for_session", _save_map)

    def _db_conn_should_not_run():
        raise AssertionError("_db_conn should not be called for road-only map scope requests")

    monkeypatch.setattr(traffic, "_db_conn", _db_conn_should_not_run)

    result = traffic.run_traffic_sql_operations(
        "traffic",
        {
            "dataset_id": "mo_week_jul13_19_2025",
            "steps": [
                {
                    "operation": "filter",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "name", "operator": "ilike", "value": "%I 70%"},
                        ],
                    },
                },
                {"operation": "generate_map", "params": {"map_type": "points", "label_column": "name"}},
                {"operation": "head", "params": {"n": 10}},
            ],
        },
    )

    assert "Applied road filter and updated map scope" in result
    assert saved["map_type"] == "traffic"
    assert saved["payload"]["roadAggregateFilter"]["road_name"] == "I 70"


def test_mixed_map_and_aggregate_skips_raw_scan_when_road_stats_available(monkeypatch):
    """
    When a query has both generate_map and a groupby/aggregate, and cv_road_stats_mv
    is available, the raw cv_points map scan should be skipped entirely.
    _try_run_road_stats_optimized handles both the table result and map saving.
    """
    import contextlib
    import pandas as pd
    from dynamic_analyst.sql import traffic
    from dynamic_analyst.sql import traffic_aggregates

    raw_scan_executed = {"called": False}
    stats_table_checked = {"called": False}

    # Patch _resolve_road_stats_table to indicate the MV exists
    def _fake_resolve_stats(conn):
        stats_table_checked["called"] = True
        return "cv_mo_20250713_20250719.cv_road_stats_mv"

    monkeypatch.setattr(traffic_aggregates, "_resolve_road_stats_table", _fake_resolve_stats)
    # traffic.py imports _resolve_road_stats_table directly, patch that reference too
    monkeypatch.setattr(traffic, "_resolve_road_stats_table", _fake_resolve_stats)

    # Intercept _build_traffic_map_sql — if called, raw scan is being executed (bad)
    from dynamic_analyst.sql import traffic_map as _tm

    def _raw_scan_called(*args, **kwargs):
        raw_scan_executed["called"] = True
        return "SELECT 1"  # dummy SQL so it doesn't explode

    monkeypatch.setattr(_tm, "_build_traffic_map_sql", _raw_scan_called)
    # Also patch the reference in traffic.py's namespace
    monkeypatch.setattr(traffic, "_build_traffic_map_sql", _raw_scan_called)

    saved = {}

    def _save_map(payload, map_type="traffic"):
        saved["payload"] = payload
        saved["map_type"] = map_type

    monkeypatch.setattr(traffic, "save_map_for_session", _save_map)
    monkeypatch.setattr(traffic_aggregates, "save_map_for_session", _save_map)
    monkeypatch.setattr(traffic, "_latest_cv_dataset_id", lambda: "cv_ds")
    monkeypatch.setattr(traffic, "get_active_session", lambda: None)
    monkeypatch.setattr(traffic, "get_last_map", lambda _sid: None)

    # Patch catalog-level functions imported into traffic's namespace to avoid real DB calls.
    # _resolve_hard_brake_table: no hard-brake source table
    monkeypatch.setattr(traffic, "_resolve_hard_brake_table", lambda conn: None)
    # _first_existing_relation: base table is cv_points
    monkeypatch.setattr(traffic, "_first_existing_relation", lambda conn, candidates: "cv_points")
    # _table_column_names: return a realistic cv_points schema
    _CV_POINTS_COLS = {"lat", "lon", "speed", "dataset_id", "road_name", "attrs"}
    monkeypatch.setattr(traffic, "_table_column_names", lambda conn, tbl: _CV_POINTS_COLS)
    # _resolve_point_coord_exprs: just return lat/lon aliases
    monkeypatch.setattr(traffic, "_resolve_point_coord_exprs", lambda conn, tbl, alias="p": (f"{alias}.lat", f"{alias}.lon"))
    # _resolve_cv_enrichment_sql: return a minimal enrichment dict
    _ENRICHMENT = {
        "joins": [],
        "road_id_expr": "NULL::text",
        "road_name_expr": "p.road_name",
        "road_ref_expr": "NULL::text",
        "speed_limit_expr": "NULL::float8",
        "way_id_expr": "NULL::bigint",
    }
    monkeypatch.setattr(traffic, "_resolve_cv_enrichment_sql", lambda conn, base_table, alias="p", **kw: _ENRICHMENT)
    # _resolve_traffic_sql_col_map: return a minimal col_map
    _COL_MAP = {
        "speed": "p.speed",
        "SpeedMPH": "p.speed",
        "SpeedLimitMPH": "NULL::float8",
        "speed_over_limit": "(p.speed - NULL::float8)",
        "vehicle_id": "NULL::text",
        "trip_id": "NULL::text",
        "timestamp": "NULL::timestamptz",
        "id": "NULL::text",
        "acc_x": "NULL::float8",
        "acc_y": "NULL::float8",
        "road_segment_id": "NULL::text",
        "road_name": "p.road_name",
    }
    monkeypatch.setattr(traffic, "_resolve_traffic_sql_col_map", lambda conn, hb, alias="p", base_table=None, **kw: dict(_COL_MAP))
    # _active_cv_run_context: no active run
    monkeypatch.setattr(traffic, "_active_cv_run_context", lambda: {})

    # Minimal DB mock: a connection that survives the `with _db_conn() as conn:` block.
    # The catalog functions are all patched above so the cursor is never actually queried.
    class _FakeCursor:
        def __init__(self):
            self.description = [("road_name",), ("point_count",), ("avg_speed_mph",)]
        def execute(self, sql, params=None): pass
        def fetchone(self): return None
        def fetchall(self): return []
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass

    class _FakeConn:
        def cursor(self, **kwargs): return _FakeCursor()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    @contextlib.contextmanager
    def _fake_db_conn():
        yield _FakeConn()

    monkeypatch.setattr(traffic, "_db_conn", _fake_db_conn)
    monkeypatch.setattr(traffic_aggregates, "_db_conn", _fake_db_conn)

    # Patch pd.read_sql_query so the stats optimizer returns a DataFrame without real DB
    monkeypatch.setattr(
        traffic_aggregates.pd,
        "read_sql_query",
        lambda sql, conn, params=None: pd.DataFrame(
            [{"road_name": "I-55", "point_count": 5000, "avg_speed_mph": 62.3}]
        ),
    )

    result = traffic.run_traffic_sql_operations(
        "traffic",
        {
            "dataset_id": "mo_week_jul13_19_2025",
            "steps": [
                {
                    "operation": "filter",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "road_name", "operator": "=", "value": "I-55"},
                        ],
                    },
                },
                {"operation": "generate_map", "params": {"map_type": "points"}},
                {
                    "operation": "groupby",
                    "params": {
                        "group_by": ["road_name"],
                        "aggregations": {"count": "count"},
                    },
                },
            ],
        },
    )

    assert not raw_scan_executed["called"], (
        "Raw cv_points map scan should have been skipped when road stats MV is available"
    )
    assert stats_table_checked["called"], "Should have checked for road stats table"
    assert result is not None
    assert saved.get("map_type") == "traffic", "Road-stats path should have saved a traffic map"


def test_run_road_stats_optimized_supports_custom_aggregate_columns(monkeypatch):
    import contextlib
    import pandas as pd
    from dynamic_analyst.sql import traffic
    from dynamic_analyst.sql import traffic_aggregates

    raw_scan_executed = {"called": False}
    stats_table_checked = {"called": False}

    def _fake_resolve_stats(conn):
        stats_table_checked["called"] = True
        return "cv_mo_20250713_20250719.cv_road_stats_mv"

    monkeypatch.setattr(traffic_aggregates, "_resolve_road_stats_table", _fake_resolve_stats)
    monkeypatch.setattr(traffic, "_resolve_road_stats_table", _fake_resolve_stats)

    from dynamic_analyst.sql import traffic_map as _tm

    def _raw_scan_called(*args, **kwargs):
        raw_scan_executed["called"] = True
        return "SELECT 1"

    monkeypatch.setattr(_tm, "_build_traffic_map_sql", _raw_scan_called)
    monkeypatch.setattr(traffic, "_build_traffic_map_sql", _raw_scan_called)

    saved = {}

    def _save_map(payload, map_type="traffic"):
        saved["payload"] = payload
        saved["map_type"] = map_type

    monkeypatch.setattr(traffic, "save_map_for_session", _save_map)
    monkeypatch.setattr(traffic_aggregates, "save_map_for_session", _save_map)
    monkeypatch.setattr(traffic, "_latest_cv_dataset_id", lambda: "cv_ds")
    monkeypatch.setattr(traffic, "get_active_session", lambda: None)
    monkeypatch.setattr(traffic, "get_last_map", lambda _sid: None)
    monkeypatch.setattr(traffic, "_resolve_hard_brake_table", lambda conn: None)
    monkeypatch.setattr(traffic, "_first_existing_relation", lambda conn, candidates: "cv_points")

    _CV_POINTS_COLS = {"lat", "lon", "speed", "dataset_id", "road_name", "attrs"}
    _CV_STATS_COLS = {
        "road_name",
        "point_count",
        "avg_speed_mph",
        "decel_03g_sum",
        "dataset_id",
    }
    monkeypatch.setattr(traffic, "_table_column_names", lambda conn, tbl: _CV_POINTS_COLS if tbl == "cv_points" else _CV_STATS_COLS)
    monkeypatch.setattr(traffic_aggregates, "_table_column_names", lambda conn, tbl: _CV_STATS_COLS)
    monkeypatch.setattr(traffic, "_resolve_point_coord_exprs", lambda conn, tbl, alias="p": (f"{alias}.lat", f"{alias}.lon"))
    _ENRICHMENT = {
        "joins": [],
        "road_id_expr": "NULL::text",
        "road_name_expr": "p.road_name",
        "road_ref_expr": "NULL::text",
        "speed_limit_expr": "NULL::float8",
        "way_id_expr": "NULL::bigint",
    }
    monkeypatch.setattr(traffic, "_resolve_cv_enrichment_sql", lambda conn, base_table, alias="p", **kw: _ENRICHMENT)
    _COL_MAP = {
        "speed": "p.speed",
        "SpeedMPH": "p.speed",
        "SpeedLimitMPH": "NULL::float8",
        "speed_over_limit": "(p.speed - NULL::float8)",
        "vehicle_id": "NULL::text",
        "trip_id": "NULL::text",
        "timestamp": "NULL::timestamptz",
        "id": "NULL::text",
        "acc_x": "NULL::float8",
        "acc_y": "NULL::float8",
        "road_segment_id": "NULL::text",
        "road_name": "p.road_name",
    }
    monkeypatch.setattr(traffic, "_resolve_traffic_sql_col_map", lambda conn, hb, alias="p", base_table=None, **kw: dict(_COL_MAP))
    monkeypatch.setattr(traffic, "_active_cv_run_context", lambda: {})

    class _FakeCursor:
        def __init__(self):
            self.description = [("road_name",), ("decel_03g_sum",)]
        def execute(self, sql, params=None): pass
        def fetchone(self): return None
        def fetchall(self): return []
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass

    class _FakeConn:
        def cursor(self, **kwargs): return _FakeCursor()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    @contextlib.contextmanager
    def _fake_db_conn():
        yield _FakeConn()

    monkeypatch.setattr(traffic, "_db_conn", _fake_db_conn)
    monkeypatch.setattr(traffic_aggregates, "_db_conn", _fake_db_conn)

    monkeypatch.setattr(
        traffic_aggregates.pd,
        "read_sql_query",
        lambda sql, conn, params=None: pd.DataFrame(
            [{"road_name": "I-35", "decel_03g_sum": 256}]
        ),
    )

    result = traffic.run_traffic_sql_operations(
        "traffic",
        {
            "dataset_id": "mo_week_jul13_19_2025",
            "steps": [
                {
                    "operation": "filter",
                    "params": {
                        "mode": "and",
                        "conditions": [
                            {"column": "road_name", "operator": "=", "value": "I-35"},
                        ],
                    },
                },
                {
                    "operation": "groupby",
                    "params": {
                        "group_by": ["road_name"],
                        "aggregations": {"decel_03g_sum": "sum"},
                    },
                },
            ],
        },
    )

    assert not raw_scan_executed["called"], (
        "Raw cv_points map scan should have been skipped when road stats MV is available"
    )
    assert stats_table_checked["called"], "Should have checked for road stats table"
    assert "I-35" in result
    assert "decel_03g_sum" in result


def test_map_without_filter_returns_error(monkeypatch):
    """
    When generate_map is requested with no filter conditions and no near-crash/near-workzone,
    run_traffic_sql_operations must return the map_requires_filter JSON error immediately
    without executing any map/scan logic.
    """
    import contextlib
    from dynamic_analyst.sql import traffic

    # Patch session helpers that are called unconditionally near the top of the function.
    monkeypatch.setattr(traffic, "_latest_cv_dataset_id", lambda: "cv_ds_latest")
    monkeypatch.setattr(traffic, "get_active_session", lambda: None)
    monkeypatch.setattr(traffic, "get_last_map", lambda _sid: None)

    # Patch catalog helpers so the with _db_conn() block doesn't need a real DB.
    monkeypatch.setattr(traffic, "_resolve_hard_brake_table", lambda conn: None)
    monkeypatch.setattr(traffic, "_first_existing_relation", lambda conn, candidates: "cv_points")
    _CV_COLS = {"lat", "lon", "speed", "dataset_id", "road_name"}
    monkeypatch.setattr(traffic, "_table_column_names", lambda conn, tbl: _CV_COLS)
    monkeypatch.setattr(traffic, "_resolve_point_coord_exprs", lambda conn, tbl, alias="p": (f"{alias}.lat", f"{alias}.lon"))
    _ENRICHMENT = {
        "joins": [],
        "road_id_expr": "NULL::text",
        "road_name_expr": "p.road_name",
        "road_ref_expr": "NULL::text",
        "speed_limit_expr": "NULL::float8",
        "way_id_expr": "NULL::bigint",
    }
    monkeypatch.setattr(traffic, "_resolve_cv_enrichment_sql", lambda conn, base_table, alias="p", **kw: _ENRICHMENT)
    _COL_MAP = {
        "speed": "p.speed",
        "SpeedMPH": "p.speed",
        "SpeedLimitMPH": "NULL::float8",
        "speed_over_limit": "(p.speed - NULL::float8)",
        "vehicle_id": "NULL::text",
        "trip_id": "NULL::text",
        "timestamp": "NULL::timestamptz",
        "id": "NULL::text",
        "acc_x": "NULL::float8",
        "acc_y": "NULL::float8",
        "road_segment_id": "NULL::text",
        "road_name": "p.road_name",
    }
    monkeypatch.setattr(traffic, "_resolve_traffic_sql_col_map", lambda conn, hb, alias="p", base_table=None, **kw: dict(_COL_MAP))
    monkeypatch.setattr(traffic, "_active_cv_run_context", lambda: {})

    # Minimal fake DB connection that satisfies the `with _db_conn() as conn:` block.
    class _FakeCursor:
        def execute(self, sql, params=None): pass
        def fetchone(self): return None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass

    class _FakeConn:
        def cursor(self, **kwargs): return _FakeCursor()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    @contextlib.contextmanager
    def _fake_db_conn():
        yield _FakeConn()

    monkeypatch.setattr(traffic, "_db_conn", _fake_db_conn)

    # Call with generate_map and NO filter conditions.
    result = traffic.run_traffic_sql_operations(
        "traffic",
        {
            "dataset_id": "mo_week_jul13_19_2025",
            "steps": [
                {"operation": "generate_map", "params": {"map_type": "points"}},
            ],
        },
    )

    assert "map_requires_filter" in result

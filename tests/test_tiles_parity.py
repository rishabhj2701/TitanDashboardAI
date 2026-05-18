import backend.routers.roads_tiles as roads_tiles
import tile_server


class _ParityCursor:
    def __init__(self, existing_relations=None):
        self._existing_relations = set(existing_relations or [])
        self._last_sql = ""
        self._last_params = None

    def execute(self, sql, params=None):
        self._last_sql = str(sql)
        self._last_params = params

    def fetchone(self):
        sql = self._last_sql
        params = self._last_params
        if "SELECT to_regclass('public.cv_runs')" in sql:
            return ("public.cv_runs",)
        if "SELECT schema_name FROM public.cv_runs WHERE run_id = %s LIMIT 1" in sql:
            return ("cv_schema_demo",)
        if "SELECT to_regclass(%s)" in sql:
            relation = params[0] if isinstance(params, (list, tuple)) and params else None
            return (relation,) if relation in self._existing_relations else (None,)
        return None


def test_tile_cache_key_keeps_router_suffix_shape():
    router_key = roads_tiles._road_tile_cache_key("cv_ds", 7, 19, 48, 20)
    tile_key = tile_server._road_tile_cache_key("cv_ds", "speed", 7, 19, 48, 20)

    assert router_key == "cv_ds:20:7:19:48"
    assert tile_key == "cv_ds:speed:20:7:19:48"
    assert tile_key.replace(":speed", "", 1) == router_key


def test_road_source_candidates_match_between_tile_server_and_router():
    existing_relations = {
        "cv_schema_demo.cv_road_stats_mv",
        "public.cv_road_stats_mv",
        "public.roads",
    }
    cur_a = _ParityCursor(existing_relations=existing_relations)
    cur_b = _ParityCursor(existing_relations=existing_relations)

    tile_candidates = tile_server._road_source_candidates(cur_a, "run_123")
    router_candidates = roads_tiles._road_source_candidates(cur_b, "run_123")

    assert tile_candidates == router_candidates
    assert tile_candidates[0] == "cv_schema_demo.cv_road_stats_mv"


def test_road_source_candidates_match_for_default_dataset_scope():
    existing_relations = {
        "public.cv_road_stats_mv",
        "public.viz_matched_roads_tbl",
    }
    cur_a = _ParityCursor(existing_relations=existing_relations)
    cur_b = _ParityCursor(existing_relations=existing_relations)

    tile_candidates = tile_server._road_source_candidates(cur_a, None)
    router_candidates = roads_tiles._road_source_candidates(cur_b, None)

    assert tile_candidates == router_candidates
    assert tile_candidates == ["public.cv_road_stats_mv", "public.viz_matched_roads_tbl"]

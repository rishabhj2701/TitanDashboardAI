"""Crash SQL domain operations."""

from typing import Any, Dict, Optional

from ..code_lookups import has_code_lookup, translate_code_value
from ..storage.postgis.table_names import APP_DATASETS, APP_EVENTS
from .common import (
    QueryablePolicyError,
    RealDictCursor,
    _CRASH_SQL_COL,
    _apply_codebook_labels,
    _build_auto_chart_payload_from_df,
    _build_groupby_bar_chart_payload,
    _compile_filter,
    _db_conn,
    _df_to_markdown_safe,
    _first_existing_relation,
    _get_column_suggestions,
    _get_event_schema_columns,
    _has_count_aggregation,
    _latest_event_dataset_id,
    _load_dataset_mapping_fields,
    _load_dataset_queryable_policy,
    _lookup_pedestrian_accident_type_codes,
    _make_crash_map_payload,
    _normalize_metric_key,
    _normalize_sort,
    _publish_chart_payload,
    _queryable_policy_guidance,
    _query_requests_chart,
    _resolve_queryable_source_column,
    _resolve_event_dataset_id,
    _resolve_sql_column_key,
    _safe_float8_expr,
    _schema_safe_column,
    _sort_needs_roads,
    json,
    logging,
    pd,
    re,
    save_map_for_session,
    traceback,
)

def get_crash_domain_schema(dataset_id: Optional[str] = None) -> dict:
    """
    Returns a schema description for the crash SQL domain so the planner knows
    which columns are valid for filtering, grouping, and aggregating.
    """
    # Core columns always available via _CRASH_SQL_COL
    core_columns = list(_CRASH_SQL_COL.keys())

    # Groupable: categorical / identifier columns
    groupable_columns = [
        "routeid", "ROUTEID", "road_segment_id", "road_name", "road", "city", "city_name",
        "county", "modot_county_nm", "year", "month", "day_of_week",
        "modot_district_abbr", "modot_district_no", "severity", "accident_date",
        "event_date", "local_hour", "accident_type", "two_veh_analysis",
        "light_condition", "weather_cond_1", "weather_cond_2", "road_surface",
        "road_condition_1", "urban_rural_class", "func_class_name",
        "intersection_type", "direction",
    ]

    # Aggregatable: numeric / count columns
    aggregate_columns = [
        "count", "no_of_vehicles", "number_of_vehicles", "number_killed",
        "number_injured", "no_disab_injury", "no_of_minor_injury",
        "at_loc_speed_limit", "lat", "lon",
    ]

    # Try to extend with dynamic schema columns from the dataset
    resolved_id = dataset_id
    dynamic_cols: list[str] = []
    try:
        if not resolved_id:
            resolved_id = _resolve_event_dataset_id(None, entity_type="crash") or _latest_event_dataset_id(entity_type="crash")
        if resolved_id:
            dynamic_cols = sorted(_get_event_schema_columns(resolved_id))
    except Exception:
        pass

    policy = _load_dataset_queryable_policy(
        resolved_id or "",
        entity_type="crash",
        available_sources=sorted(set(core_columns + dynamic_cols)),
    )
    queryable_columns = sorted(
        {
            str(item.get("query_name", "")).strip()
            for item in policy.get("fields") or []
            if isinstance(item, dict) and bool(item.get("enabled", True))
        }
    )
    all_columns = queryable_columns or sorted(set(core_columns + dynamic_cols))
    all_col_set = set(all_columns)
    groupable_columns = [col for col in groupable_columns if col in all_col_set]
    aggregate_columns = [col for col in aggregate_columns if col == "count" or col in all_col_set]

    return {
        "domain": "crash",
        "dataset_id": resolved_id,
        "columns": all_columns,
        "groupable_columns": groupable_columns,
        "aggregate_columns": aggregate_columns,
        "allowed_aggregations": ["count", "sum", "mean", "avg", "min", "max"],
        "notes": [
            "Use 'count' aggregation to count crash records.",
            "The 'severity' column contains crash severity codes (may require codebook translation).",
            "The 'accident_type' column contains coded accident type values.",
            "The 'two_veh_analysis' column contains coded values translated by the codebook (e.g. 'FRONT TO FRONT', 'FRONT TO REAR', 'ANGLE', 'SIDESWIPE (SAME DIRECTION)'). Use unique_values on this column first if unsure of exact values.",
            "Date/time columns: accident_date, accident_time, event_date, local_hour.",
            "Location columns: road_name, city, county, modot_district_no, modot_district_abbr.",
            "Injury/fatality columns: number_killed, number_injured, no_disab_injury, no_of_minor_injury.",
            "Environmental columns: light_condition, weather_cond_1, weather_cond_2, road_surface, road_condition_1.",
            "Only queryable fields configured for this dataset are available.",
        ],
    }


def run_crash_sql_operations(dataset: str, query: Dict[str, Any]) -> str:
    """
    SQL-backed execution for the CRASH/EVENTS dataset (PostGIS events table).
    Supports: filter, groupby, aggregate, unique_values, sort, head, generate_map.
    """
    log = logging.getLogger("adk_server")
    try:
        if dataset.lower() not in {"crash", "crashes", "event", "events"}:
            raise ValueError("SQL crash mode supports datasets: crash/crashes/event/events.")

        entity_type = "crash" if dataset.lower() in {"crash", "crashes"} else "event"
        dataset_id_input = query.get("dataset_id")
        dataset_id = _resolve_event_dataset_id(dataset_id_input, entity_type=entity_type)
        if not dataset_id:
            dataset_id = _latest_event_dataset_id(entity_type=entity_type)
        if not dataset_id and entity_type == "crash":
            dataset_id = _resolve_event_dataset_id(dataset_id_input, entity_type="event") or _latest_event_dataset_id(entity_type="event")
        if not dataset_id:
            raise ValueError(f"No {entity_type} dataset_id found for this session.")

        try:
            with _db_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_type
                    FROM """ + APP_DATASETS + """
                    WHERE dataset_id=%s
                    LIMIT 1
                    """,
                    (dataset_id,),
                )
                row = cur.fetchone()
                resolved_entity = str((row or [None])[0] or "").strip().lower()
            if resolved_entity and resolved_entity not in {entity_type, "event", "crash"}:
                fallback = _latest_event_dataset_id(entity_type=entity_type)
                if fallback:
                    log.warning(
                        "crash_sql_dataset_entity_mismatch dataset_id=%s entity=%s expected=%s; using %s",
                        dataset_id,
                        resolved_entity,
                        entity_type,
                        fallback,
                    )
                    dataset_id = fallback
                else:
                    raise ValueError(
                        f"Dataset '{dataset_id}' is entity_type '{resolved_entity}', not '{entity_type}'. "
                        "Use list_datasets and pass a crash dataset_id, or ask a CV question with domain=traffic."
                    )
        except ValueError:
            raise
        except Exception:
            pass

        log.info(
            json.dumps(
                {
                    "event": "crash_dataset_resolved",
                    "dataset_input": dataset_id_input,
                    "dataset_id": dataset_id,
                    "entity_type": entity_type,
                },
                default=str,
            )
        )

        reasoning = query.get("reasoning", "")
        steps = query.get("steps", []) or []

        def _safe_sql_alias(raw: Any, default: str = "count") -> str:
            alias = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or "").strip().lower())
            alias = alias.strip("_")
            if not alias:
                alias = default
            if alias[0].isdigit():
                alias = f"c_{alias}"
            return alias

        def _iter_agg_specs(aggs: dict, *, mode: str) -> list[dict[str, str]]:
            specs: list[dict[str, str]] = []
            for raw_key, raw_val in (aggs or {}).items():
                agg_key = str(raw_key or "").strip() or "count"
                alias_override: Optional[str] = None
                col = agg_key
                fn = ""

                if isinstance(raw_val, dict):
                    fn = str(
                        raw_val.get("fn")
                        or raw_val.get("function")
                        or raw_val.get("agg")
                        or ""
                    ).strip().lower()
                    col = str(raw_val.get("column") or raw_val.get("col") or agg_key).strip() or agg_key
                    alias_raw = str(raw_val.get("alias") or "").strip()
                    alias_override = alias_raw or None
                else:
                    fn = str(raw_val or "").strip().lower()
                    m = re.match(r"^\s*([a-z_]+)\s*\(\s*([a-zA-Z0-9_*]+)\s*\)\s*$", fn)
                    if m:
                        fn = m.group(1).strip().lower()
                        inner_col = m.group(2).strip()
                        col = "count" if inner_col == "*" else inner_col

                if col == "*":
                    col = "count"
                if not fn:
                    raise ValueError(f"Missing aggregation fn for '{agg_key}' in SQL crash {mode} mode")

                specs.append(
                    {
                        "key": agg_key,
                        "column": col or agg_key,
                        "fn": fn,
                        "alias": alias_override or "",
                    }
                )
            return specs

        filters: list[dict] = []
        filter_mode = "and"
        map_req: Optional[dict] = None
        groupby_req: Optional[dict] = None
        aggregate_req: Optional[dict] = None
        unique_values_req: Optional[dict] = None
        sort_req: Optional[dict] = None
        head_req: Optional[int] = None
        resolved_group_cols_for_chart: list[str] = []

        for step in steps:
            op = (step.get("operation") or "").lower()
            params = step.get("params", {}) or {}
            if op == "filter":
                filters.extend(params.get("conditions", []) or [])
                filter_mode = params.get("mode", filter_mode) or filter_mode
            elif op == "generate_map":
                map_req = params
            elif op == "groupby":
                groupby_req = params
            elif op == "aggregate":
                aggregate_req = params
            elif op == "unique_values":
                unique_values_req = params
            elif op == "sort":
                sort_req = params
            elif op == "head":
                head_req = int(params.get("n", 10))
            else:
                raise ValueError(
                    f"SQL crash mode does not support operation '{op}' yet. "
                    "Supported: filter, generate_map, groupby, aggregate, unique_values, sort, head."
                )

        where_parts = ["e.dataset_id = %s"]
        where_params: list = [dataset_id]

        crash_col_map = dict(_CRASH_SQL_COL)
        with _db_conn() as conn:
            roads_table = _first_existing_relation(conn, ["public.roads", "roads"])
        if not roads_table:
            road_expr_no_join = (
                "COALESCE("
                "NULLIF(e.props->>'road',''), "
                "NULLIF(e.props->>'road_name',''), "
                "NULLIF(e.props->>'roadName','')"
                ")"
            )
            crash_col_map["road"] = road_expr_no_join
            crash_col_map["road_name"] = road_expr_no_join
            crash_col_map["name"] = road_expr_no_join
        schema_cols = _get_event_schema_columns(dataset_id)
        mapping_fields = _load_dataset_mapping_fields(dataset_id)
        numeric_ops = {">", ">=", "<", "<="}

        def _props_expr(source_col: Optional[str], *, numeric: bool = False) -> Optional[str]:
            col = str(source_col or "").strip()
            if not col or not re.match(r"^[A-Za-z0-9_]+$", col):
                return None
            expr = f"NULLIF(e.props->>'{col}','')"
            return _safe_float8_expr(expr) if numeric else expr

        mapped_primary_expr = _props_expr(mapping_fields.get("primary_id"))
        if mapped_primary_expr:
            crash_col_map["primary_id"] = f"COALESCE({mapped_primary_expr}, {crash_col_map['primary_id']})"

        mapped_event_date_expr = _props_expr(mapping_fields.get("event_date"))
        if mapped_event_date_expr:
            crash_col_map["event_date"] = f"COALESCE({crash_col_map['event_date']}, {mapped_event_date_expr})"
            crash_col_map["accident_date"] = f"COALESCE({crash_col_map['accident_date']}, {mapped_event_date_expr})"

        mapped_event_time_expr = _props_expr(mapping_fields.get("event_time"))
        if mapped_event_time_expr:
            crash_col_map["event_time"] = f"COALESCE({crash_col_map['event_time']}, {mapped_event_time_expr})"
            crash_col_map["accident_time"] = f"COALESCE({crash_col_map['accident_time']}, {mapped_event_time_expr})"

        mapped_timestamp_expr = _props_expr(mapping_fields.get("timestamp"))
        if mapped_timestamp_expr:
            crash_col_map["timestamp"] = f"COALESCE(CAST(e.ts AS text), {mapped_timestamp_expr})"

        mapped_road_id_expr = _props_expr(mapping_fields.get("road_id"))
        if mapped_road_id_expr:
            crash_col_map["road_segment_id"] = f"COALESCE(NULLIF(e.road_segment_id,''), {mapped_road_id_expr})"

        mapped_road_name_expr = _props_expr(mapping_fields.get("road_name"))

        queryable_policy = _load_dataset_queryable_policy(
            dataset_id,
            entity_type=entity_type,
            available_sources=sorted(set(schema_cols) | set(crash_col_map.keys())),
        )
        queryable_alias_map = queryable_policy.get("alias_map") if isinstance(queryable_policy, dict) else {}
        if not isinstance(queryable_alias_map, dict):
            queryable_alias_map = {}
        enabled_query_names = queryable_policy.get("enabled_query_names") if isinstance(queryable_policy, dict) else []
        if not isinstance(enabled_query_names, list):
            enabled_query_names = []

        def _normalize_col_key(text: Any) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(text or "").strip().lower())

        def _iter_leaf_conditions(conditions: list[dict]):
            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                nested = condition.get("conditions")
                if isinstance(nested, list):
                    yield from _iter_leaf_conditions(nested)
                else:
                    yield condition

        def _reasoning_requests_pedestrian_fatality(analysis_text: str, conditions: list[dict]) -> bool:
            payload_text = f"{analysis_text} {json.dumps(conditions, default=str)}".lower()
            has_ped = "pedestrian" in payload_text
            has_fatal = any(tok in payload_text for tok in ("fatal", "fatality", "killed", "death"))
            return bool(has_ped and has_fatal)

        def _has_number_killed_filter(conditions: list[dict]) -> bool:
            for condition in _iter_leaf_conditions(conditions):
                col_norm = _normalize_col_key(condition.get("column"))
                if col_norm not in {"numberkilled", "killed", "fatalities", "fatalitycount"}:
                    continue
                op = str(condition.get("operator") or "").strip().lower()
                val = condition.get("value")
                if op in {">", ">=", "==", "="} and isinstance(val, (int, float)) and float(val) >= 0:
                    return True
                if op == "between" and isinstance(val, (list, tuple)) and len(val) == 2:
                    low, high = val
                    if isinstance(high, (int, float)) and float(high) > 0:
                        return True
            return False

        def _has_accident_type_filter(conditions: list[dict]) -> bool:
            for condition in _iter_leaf_conditions(conditions):
                col_norm = _normalize_col_key(condition.get("column"))
                if col_norm in {"accidenttype", "crashtype"}:
                    return True
            return False

        def _rewrite_pedestrian_alias_filters(conditions: list[dict]) -> list[dict]:
            rewritten: list[dict] = []
            pedestrian_aliases = {
                "pedestriankilledcount",
                "pedestrianfatality",
                "pedestrianfatalities",
                "pedestrianfatalitycount",
                "pedestriankilled",
                "pedkilled",
                "pedestrianskilled",
                "numberpedkilled",
                "numberpedestrianskilled",
                "pedestriandeathcount",
                "pedfatality",
            }
            ped_codes_cache: Optional[list[str]] = None

            def _pedestrian_codes() -> list[str]:
                nonlocal ped_codes_cache
                if ped_codes_cache is None:
                    ped_codes_cache = _lookup_pedestrian_accident_type_codes()
                return ped_codes_cache

            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                nested = condition.get("conditions")
                if isinstance(nested, list):
                    rebuilt_nested = _rewrite_pedestrian_alias_filters(nested)
                    if rebuilt_nested:
                        copied = dict(condition)
                        copied["conditions"] = rebuilt_nested
                        rewritten.append(copied)
                    continue
                col_norm = _normalize_col_key(condition.get("column"))
                if col_norm in pedestrian_aliases:
                    ped_codes = _pedestrian_codes()
                    if not ped_codes:
                        raise ValueError(
                            "Pedestrian fatality filter needs accident-type code mappings. "
                            "Upload/match the codebook so accident_type pedestrian codes can be resolved."
                        )
                    rewritten.append(
                        {
                            "conditions": [
                                {"column": "number_killed", "operator": ">", "value": 0},
                                {"column": "accident_type", "operator": "in", "value": ped_codes},
                            ],
                            "mode": "and",
                        }
                    )
                    continue
                rewritten.append(condition)
            return rewritten

        def _normalize_accident_type_filter_values(conditions: list[dict]) -> list[dict]:
            rewritten: list[dict] = []

            def _code_variants(value: Any) -> list[str]:
                text = str(value or "").strip()
                if not text:
                    return []
                out = [text]
                if re.fullmatch(r"\d+", text):
                    out.append(text.lstrip("0") or "0")
                # de-duplicate preserving order
                return list(dict.fromkeys([v for v in out if v]))

            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                nested = condition.get("conditions")
                if isinstance(nested, list):
                    copied = dict(condition)
                    copied["conditions"] = _normalize_accident_type_filter_values(nested)
                    rewritten.append(copied)
                    continue
                col_norm = _normalize_col_key(condition.get("column"))
                op = str(condition.get("operator") or "").strip().lower()
                if col_norm == "accidenttype":
                    value = condition.get("value")
                    if op in {"==", "="}:
                        variants = _code_variants(value)
                        if len(variants) > 1:
                            copied = dict(condition)
                            copied["operator"] = "in"
                            copied["value"] = variants
                            rewritten.append(copied)
                            continue
                    elif op == "in" and isinstance(value, (list, tuple)):
                        variants: list[str] = []
                        for item in value:
                            variants.extend(_code_variants(item))
                        copied = dict(condition)
                        copied["value"] = list(dict.fromkeys(variants))
                        rewritten.append(copied)
                        continue
                rewritten.append(condition)
            return rewritten

        filters = _rewrite_pedestrian_alias_filters(filters)
        filters = _normalize_accident_type_filter_values(filters)
        if _reasoning_requests_pedestrian_fatality(reasoning, filters):
            ped_codes = _lookup_pedestrian_accident_type_codes()
            if not ped_codes:
                raise ValueError(
                    "Could not resolve pedestrian accident-type codes from codebook for this session. "
                    "Upload/match the attributes codebook and retry."
                )
            augmented = False
            if not _has_number_killed_filter(filters):
                filters.append({"column": "number_killed", "operator": ">", "value": 0})
                augmented = True
            if not _has_accident_type_filter(filters):
                filters.append({"column": "accident_type", "operator": "in", "value": ped_codes})
                augmented = True
            if augmented:
                log.info(
                    json.dumps(
                        {
                            "event": "crash_semantic_filter_augmented",
                            "intent": "pedestrian_fatality",
                            "dataset_id": dataset_id,
                            "pedestrian_code_count": len(ped_codes),
                        },
                        default=str,
                    )
                )

        def _ensure_event_col(col: str, *, numeric: bool = False) -> None:
            if not col or col in crash_col_map:
                return
            target = _schema_safe_column(col, schema_cols)
            if not target:
                return
            if not re.match(r"^[A-Za-z0-9_]+$", target):
                return
            base_expr = f"NULLIF(e.props->>'{target}','')"
            expr = _safe_float8_expr(base_expr) if numeric else base_expr
            crash_col_map[col] = expr

        for field in queryable_policy.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if not bool(field.get("enabled", True)):
                continue
            query_name = str(field.get("query_name") or "").strip()
            source_col = str(field.get("source_column") or "").strip()
            if not query_name or not source_col:
                continue
            if source_col not in crash_col_map:
                _ensure_event_col(source_col, numeric=False)
            if source_col in crash_col_map and query_name not in crash_col_map:
                crash_col_map[query_name] = crash_col_map[source_col]

        def _require_queryable(col_name: Any, *, allow_count: bool = False) -> str:
            text = str(col_name or "").strip()
            if allow_count and _normalize_metric_key(text) == "count":
                return "count"
            source_col = _resolve_queryable_source_column(text, queryable_alias_map)
            if not source_col:
                raise QueryablePolicyError(_queryable_policy_guidance(text, enabled_query_names))
            if source_col not in crash_col_map and _schema_safe_column(source_col, schema_cols) is None:
                raise QueryablePolicyError(_queryable_policy_guidance(text, enabled_query_names))
            return source_col

        for cond in _iter_leaf_conditions(filters):
            col = (cond.get("column") or "").strip()
            if not col:
                continue
            source_col = _require_queryable(col)
            if source_col != col:
                cond["column"] = source_col
            col = source_col
            if col in crash_col_map:
                continue
            op = (cond.get("operator") or "").strip().lower()
            val = cond.get("value", None)
            is_numeric = op in numeric_ops
            if op == "between" and isinstance(val, (list, tuple)) and len(val) == 2:
                is_numeric = all(isinstance(v, (int, float)) for v in val)
            if isinstance(val, (int, float)):
                is_numeric = True
            _ensure_event_col(col, numeric=is_numeric)

        def _find_unknown_filter_column(conditions: list[dict]) -> Optional[str]:
            for condition in conditions or []:
                if "conditions" in condition:
                    nested = _find_unknown_filter_column(condition.get("conditions") or [])
                    if nested:
                        return nested
                    continue
                col_name = (condition.get("column") or "").strip()
                if col_name and col_name not in crash_col_map:
                    return col_name
            return None

        unknown_filter_col = _find_unknown_filter_column(filters)
        if unknown_filter_col:
            suggestions = _get_column_suggestions(unknown_filter_col, schema_cols)
            raise ValueError(f"Unsupported filter column '{unknown_filter_col}' in SQL crash mode.{suggestions}")

        # --- CODEBOOK REVERSE LOOKUP ---
        # If the user filters on a coded column using a human-readable label (e.g.
        # two_veh_analysis = "HEAD TO HEAD"), translate that label back to the raw
        # stored code(s) before building the SQL WHERE clause.
        def _reverse_codebook_filters(conditions: list[dict]) -> list[dict]:
            """Rewrite filter values that are codebook labels → raw codes."""
            try:
                from .. import postgis_store as _ps
                col_names = list({
                    (cond.get("column") or "").strip()
                    for cond in _iter_leaf_conditions(conditions)
                    if (cond.get("column") or "").strip()
                })
                cb_map = _ps.get_codebook_map_for_columns(col_names, dataset_id=dataset_id)
            except Exception:
                return conditions

            if not cb_map:
                return conditions

            def _rewrite_cond(cond: dict) -> dict:
                if not isinstance(cond, dict):
                    return cond
                nested = cond.get("conditions")
                if isinstance(nested, list):
                    copied = dict(cond)
                    copied["conditions"] = [_rewrite_cond(c) for c in nested]
                    return copied
                col = (cond.get("column") or "").strip()
                lookup = cb_map.get(col)  # {code: label}
                if not lookup:
                    return cond
                # Build reverse map: normalized_label → [code, ...]
                reverse: dict[str, list[str]] = {}
                for code, label in lookup.items():
                    key = label.strip().upper()
                    reverse.setdefault(key, []).append(code)
                op = str(cond.get("operator") or "").strip().lower()
                value = cond.get("value")
                if op in ("=", "==", "eq", "ilike", "like") and isinstance(value, str):
                    norm = value.strip().upper().strip("%")
                    codes = reverse.get(norm)
                    if codes:
                        copied = dict(cond)
                        if len(codes) == 1:
                            copied["operator"] = "="
                            copied["value"] = codes[0]
                        else:
                            copied["operator"] = "in"
                            copied["value"] = codes
                        return copied
                elif op == "in" and isinstance(value, list):
                    resolved_codes: list[str] = []
                    all_resolved = True
                    for v in value:
                        norm = str(v).strip().upper()
                        codes = reverse.get(norm)
                        if codes:
                            resolved_codes.extend(codes)
                        else:
                            # Not a label — try treating as raw code
                            if str(v).strip() in lookup:
                                resolved_codes.append(str(v).strip())
                            else:
                                all_resolved = False
                                resolved_codes.append(str(v))
                    if resolved_codes:
                        copied = dict(cond)
                        copied["value"] = list(dict.fromkeys(resolved_codes))
                        return copied
                return cond

            return [_rewrite_cond(c) for c in conditions]

        filters = _reverse_codebook_filters(filters)

        # --- CATEGORICAL VALUE ALIGNMENT (BOUNDED) ---
        # For string equality filters (e.g., severity="Fatal"), align user-provided
        # values to actual dataset categories using a capped distinct-value sample.
        # This avoids brittle exact/case matches while keeping token/DB usage bounded.
        _MAX_CATEGORY_CANDIDATES = 200
        _MAX_CATEGORY_MATCHES = 8
        categorical_candidates_cache: dict[str, list[str]] = {}

        def _normalize_match_text(value: Any) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

        def _tokenize_match_text(value: Any) -> list[str]:
            return [tok for tok in re.findall(r"[a-z0-9]+", str(value or "").strip().lower()) if tok]

        def _get_categorical_candidates(col: str) -> list[str]:
            if col in categorical_candidates_cache:
                return categorical_candidates_cache[col]
            expr = crash_col_map.get(col)
            if not expr:
                categorical_candidates_cache[col] = []
                return []
            from_sql_candidates = f"{APP_EVENTS} e"
            if "r." in expr and roads_table:
                from_sql_candidates += f" LEFT JOIN {roads_table} r ON r.road_segment_id = e.road_segment_id"
            sql = f"""
                SELECT CAST({expr} AS text) AS value, COUNT(*) AS cnt
                FROM {from_sql_candidates}
                WHERE e.dataset_id = %s
                  AND {expr} IS NOT NULL
                  AND CAST({expr} AS text) <> ''
                GROUP BY CAST({expr} AS text)
                ORDER BY cnt DESC
                LIMIT %s
            """
            try:
                with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, [dataset_id, _MAX_CATEGORY_CANDIDATES])
                    rows = cur.fetchall() or []
                vals = [str((row or {}).get("value") or "").strip() for row in rows]
                categorical_candidates_cache[col] = [v for v in vals if v]
            except Exception:
                categorical_candidates_cache[col] = []
            return categorical_candidates_cache[col]

        def _match_categorical_values(raw_value: Any, candidates: list[str]) -> list[str]:
            text = str(raw_value or "").strip()
            if not text or not candidates:
                return []
            norm = _normalize_match_text(text)
            if not norm:
                return []

            # 1) Exact normalized match (case/punctuation-insensitive)
            exact = [c for c in candidates if _normalize_match_text(c) == norm]
            if exact:
                return exact[:1]

            # 2) Substring containment on normalized forms (e.g., "fatal" in "fatalinjury")
            contains = [
                c for c in candidates
                if norm in _normalize_match_text(c) or _normalize_match_text(c) in norm
            ]
            if contains:
                return contains[:_MAX_CATEGORY_MATCHES]

            # 3) Token overlap scoring for slightly looser phrasing
            query_tokens = set(_tokenize_match_text(text))
            if not query_tokens:
                return []
            scored: list[tuple[int, str]] = []
            for candidate in candidates:
                cand_tokens = set(_tokenize_match_text(candidate))
                if not cand_tokens:
                    continue
                overlap = len(query_tokens & cand_tokens)
                if overlap > 0:
                    scored.append((overlap, candidate))
            if not scored:
                return []
            scored.sort(key=lambda pair: (-pair[0], pair[1]))
            return [c for _, c in scored[:_MAX_CATEGORY_MATCHES]]

        def _align_categorical_filters(conditions: list[dict]) -> list[dict]:
            rewritten: list[dict] = []
            for cond in conditions or []:
                if not isinstance(cond, dict):
                    continue
                nested = cond.get("conditions")
                if isinstance(nested, list):
                    copied = dict(cond)
                    copied["conditions"] = _align_categorical_filters(nested)
                    rewritten.append(copied)
                    continue

                col = str(cond.get("column") or "").strip()
                op = str(cond.get("operator") or "").strip().lower()
                if not col or col not in crash_col_map:
                    rewritten.append(cond)
                    continue

                if op in {"=", "==", "eq", "ilike", "like"} and isinstance(cond.get("value"), str):
                    candidates = _get_categorical_candidates(col)
                    matches = _match_categorical_values(cond.get("value"), candidates)
                    if matches:
                        copied = dict(cond)
                        if len(matches) == 1:
                            copied["operator"] = "="
                            copied["value"] = matches[0]
                        else:
                            copied["operator"] = "in"
                            copied["value"] = matches
                        rewritten.append(copied)
                        continue

                if op == "in" and isinstance(cond.get("value"), list):
                    candidates = _get_categorical_candidates(col)
                    merged: list[str] = []
                    for item in cond.get("value") or []:
                        matches = _match_categorical_values(item, candidates)
                        if matches:
                            merged.extend(matches)
                        else:
                            merged.append(str(item))
                    merged = list(dict.fromkeys([m for m in merged if str(m).strip()]))
                    if merged:
                        copied = dict(cond)
                        copied["value"] = merged
                        rewritten.append(copied)
                        continue

                rewritten.append(cond)
            return rewritten

        filters = _align_categorical_filters(filters)

        filt_clause, filt_params, needs_roads = _compile_filter(filters, filter_mode, crash_col_map)
        if filt_clause:
            where_parts.append(f"({filt_clause})")
            where_params.extend(filt_params)

        join_roads = needs_roads
        if groupby_req:
            join_roads = join_roads or any(
                (_resolve_queryable_source_column(c, queryable_alias_map) or c) in ("road", "road_name", "name")
                for c in (groupby_req.get("group_by") or [])
            )
        if unique_values_req:
            unique_col_candidate = str(
                unique_values_req.get("column")
                or unique_values_req.get("col")
                or ""
            ).strip()
            if unique_col_candidate:
                resolved_unique_join_col = _resolve_queryable_source_column(unique_col_candidate, queryable_alias_map) or _resolve_sql_column_key(unique_col_candidate, crash_col_map)
                join_roads = join_roads or (resolved_unique_join_col in ("road", "road_name", "name"))
        if sort_req:
            join_roads = join_roads or _sort_needs_roads(sort_req)
        from_sql = f"{APP_EVENTS} e"
        if join_roads and roads_table:
            from_sql += f" LEFT JOIN {roads_table} r ON r.road_segment_id = e.road_segment_id"

        road_name_parts = []
        if join_roads and roads_table:
            road_name_parts.append("r.name")
        if mapped_road_name_expr:
            road_name_parts.append(mapped_road_name_expr)
        road_name_parts.extend(
            [
                "NULLIF(e.props->>'road','')",
                "NULLIF(e.props->>'road_name','')",
                "NULLIF(e.props->>'roadName','')",
            ]
        )
        road_name_expr = "COALESCE(" + ", ".join(road_name_parts) + ")"

        where_sql = " AND ".join(where_parts)

        map_rows_plotted: Optional[int] = None

        # 1) MAP SIDE-EFFECT
        if map_req is not None:
            limit = int(map_req.get("limit", 10000))
            grid_deg = float(map_req.get("grid_deg", 0.0015))
            per_cell = int(map_req.get("per_cell", 5))

            from_sql_map = f"{APP_EVENTS} e"
            if roads_table:
                from_sql_map += f" LEFT JOIN {roads_table} r ON r.road_segment_id = e.road_segment_id"
            road_name_map_parts = []
            if roads_table:
                road_name_map_parts.append("r.name")
            if mapped_road_name_expr:
                road_name_map_parts.append(mapped_road_name_expr)
            road_name_map_parts.extend(
                [
                    "NULLIF(e.props->>'road','')",
                    "NULLIF(e.props->>'road_name','')",
                    "NULLIF(e.props->>'roadName','')",
                ]
            )
            road_name_expr_map = "COALESCE(" + ", ".join(road_name_map_parts) + ")"

            map_sql = f"""
                WITH filtered AS (
                SELECT
                    e.lat AS latitude,
                    e.lon AS longitude,
                    e.ts AS timestamp,
                    e.road_segment_id,
                    {road_name_expr_map} AS road_name,
                    {crash_col_map["severity"]} AS severity,
                    {crash_col_map["primary_id"]} AS primary_id,
                    {crash_col_map["hp_acc_image_no"]} AS hp_acc_image_no,
                    {crash_col_map["accident_date"]} AS accident_date,
                    {crash_col_map["accident_time"]} AS accident_time,

                    FLOOR(e.lat / %s) AS lat_bin,
                    FLOOR(e.lon / %s) AS lon_bin,

                    md5(
                    COALESCE(e.id::text,'') ||
                    COALESCE(e.ts::text,'') ||
                    COALESCE({road_name_expr_map}::text,'')
                    ) AS stable_key
                FROM {from_sql_map}
                WHERE {where_sql}
                    AND e.lat IS NOT NULL
                    AND e.lon IS NOT NULL
                ),
                ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                    PARTITION BY lat_bin, lon_bin
                    ORDER BY stable_key
                    ) AS rn
                FROM filtered
                )
                SELECT
                latitude, longitude, timestamp, road_segment_id, road_name,
                severity, primary_id, hp_acc_image_no, accident_date, accident_time
                FROM ranked
                WHERE rn <= %s
                ORDER BY stable_key
                LIMIT %s
            """

            with _db_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(map_sql, [grid_deg, grid_deg] + where_params + [per_cell, limit])
                rows = cur.fetchall()
                map_rows_plotted = len(rows)

            save_map_for_session(_make_crash_map_payload(rows, label="Crash Map"), map_type="crash")

            log.info(json.dumps({
                "event": "sql_compiled_map",
                "dataset": dataset,
                "dataset_id": dataset_id,
                "sql": map_sql.strip()[:2500],
                "params": ([grid_deg, grid_deg] + where_params + [per_cell, limit])
            }, default=str))

        # 2) FINAL TABLE RESULT
        sort_cols, sort_dirs = _normalize_sort(sort_req)
        if unique_values_req is not None:
            requested_col = str(
                unique_values_req.get("column")
                or unique_values_req.get("col")
                or ""
            ).strip()
            if not requested_col:
                raise ValueError("unique_values requires params.column")
            requested_col = _require_queryable(requested_col)

            resolved_unique_col = _resolve_sql_column_key(requested_col, crash_col_map)
            if resolved_unique_col is None:
                _ensure_event_col(requested_col, numeric=False)
                resolved_unique_col = _resolve_sql_column_key(requested_col, crash_col_map)
            if resolved_unique_col is None:
                suggestions = _get_column_suggestions(requested_col, schema_cols)
                raise ValueError(f"Unsupported unique_values column '{requested_col}' in SQL crash mode.{suggestions}")

            include_nulls = bool(
                unique_values_req.get("include_nulls")
                or unique_values_req.get("include_null")
            )
            limit_raw = unique_values_req.get("limit", unique_values_req.get("n", 100))
            try:
                unique_limit = int(limit_raw)
            except (TypeError, ValueError):
                unique_limit = 100
            if head_req is not None:
                unique_limit = min(unique_limit, int(head_req))
            unique_limit = max(1, min(100, unique_limit))

            raw_value_expr = f"NULLIF(TRIM(CAST({crash_col_map[resolved_unique_col]} AS text)), '')"
            grouped_value_expr = (
                f"COALESCE({raw_value_expr}, '[NULL]')" if include_nulls else raw_value_expr
            )
            value_alias = resolved_unique_col

            base_where_sql = where_sql
            base_where_params = list(where_params)
            if not include_nulls:
                base_where_sql = f"{base_where_sql} AND {raw_value_expr} IS NOT NULL"

            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    key_norm = _normalize_metric_key(col)
                    if key_norm == "count":
                        order_expr = "count"
                    elif key_norm in {"value", _normalize_metric_key(requested_col), _normalize_metric_key(value_alias)}:
                        order_expr = value_alias
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for unique_values result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"
            else:
                order_clause = f" ORDER BY count DESC, {value_alias} ASC"

            sql = f"""
                SELECT
                    {grouped_value_expr} AS {value_alias},
                    COUNT(*) AS count
                FROM {from_sql}
                WHERE {base_where_sql}
                GROUP BY {grouped_value_expr}
                {order_clause}
                LIMIT %s
            """
            where_params = base_where_params + [unique_limit]
        elif groupby_req is not None:
            raw_group_cols = groupby_req.get("group_by") or []
            group_cols = [_require_queryable(col) for col in raw_group_cols]
            resolved_group_cols_for_chart = list(group_cols)
            aggs = groupby_req.get("aggregations") or {}
            if not group_cols or not aggs:
                raise ValueError("groupby requires group_by and aggregations")

            gb_exprs = []
            gb_selects = []
            for c in group_cols:
                if c not in crash_col_map:
                    _ensure_event_col(c, numeric=False)
                if c not in crash_col_map:
                    suggestions = _get_column_suggestions(c, schema_cols)
                    raise ValueError(f"Unsupported group_by column '{c}' in SQL crash mode.{suggestions}")
                gb_exprs.append(crash_col_map[c])
                gb_selects.append(f"{crash_col_map[c]} AS {c}")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_alias_map: dict[str, str] = {}
            for spec in _iter_agg_specs(aggs, mode="groupby"):
                agg_key = spec["key"]
                fn_check = spec["fn"]
                raw_col = spec["column"]
                if fn_check == "count":
                    col = _resolve_queryable_source_column(raw_col, queryable_alias_map) or "count"
                else:
                    col = _require_queryable(raw_col)
                alias_override = spec["alias"] or None
                if col not in crash_col_map and col != "count":
                    numeric = fn_check in ("mean", "avg", "sum")
                    _ensure_event_col(col, numeric=numeric)
                if col not in crash_col_map and col != "count" and fn_check != "count":
                    suggestions = _get_column_suggestions(col, schema_cols)
                    raise ValueError(f"Unsupported aggregation column '{col}' in SQL crash mode.{suggestions}")
                fn = fn_check
                if col == "count":
                    alias = alias_override or ("count" if agg_key == "count" else _safe_sql_alias(agg_key, default="count"))
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                if col not in crash_col_map and fn == "count":
                    alias = alias_override or _safe_sql_alias(agg_key or col, default="count")
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                expr = crash_col_map[col]
                if fn in ("mean", "avg"):
                    alias = alias_override or f"{col}_avg"
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "sum":
                    alias = alias_override or f"{col}_sum"
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "min":
                    alias = alias_override or f"{col}_min"
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "max":
                    alias = alias_override or f"{col}_max"
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "count":
                    alias = alias_override or f"{col}_count"
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                    agg_aliases.append(alias)
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL crash mode")
                agg_alias_map[agg_key] = alias
                agg_alias_map[col] = alias

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    resolved_sort_col = _resolve_queryable_source_column(col, queryable_alias_map) or col
                    if resolved_sort_col in group_cols:
                        order_expr = resolved_sort_col
                    elif col in agg_aliases:
                        order_expr = col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    elif resolved_sort_col in agg_alias_map:
                        order_expr = agg_alias_map[resolved_sort_col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for groupby result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            limit_clause = ""
            if head_req is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(head_req)]

            sql = f"""
                SELECT {", ".join(gb_selects + agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                GROUP BY {", ".join(gb_exprs)}
                {order_clause}
                {limit_clause}
            """
        elif aggregate_req is not None:
            aggs = aggregate_req.get("aggregations") or {}
            if not aggs:
                raise ValueError("aggregate requires aggregations")

            agg_selects = []
            agg_aliases: list[str] = []
            agg_alias_map: dict[str, str] = {}
            for spec in _iter_agg_specs(aggs, mode="aggregate"):
                agg_key = spec["key"]
                fn_check = spec["fn"]
                raw_col = spec["column"]
                if fn_check == "count":
                    col = _resolve_queryable_source_column(raw_col, queryable_alias_map) or "count"
                else:
                    col = _require_queryable(raw_col)
                alias_override = spec["alias"] or None
                if col not in crash_col_map and col != "count":
                    # Try to add dynamically
                    numeric = fn_check in ("mean", "avg", "sum")
                    _ensure_event_col(col, numeric=numeric)
                if col not in crash_col_map and col != "count" and fn_check != "count":
                    suggestions = _get_column_suggestions(col, schema_cols)
                    raise ValueError(f"Unsupported aggregation column '{col}' in SQL crash mode.{suggestions}")
                fn = fn_check
                if col == "count":
                    alias = alias_override or ("count" if agg_key == "count" else _safe_sql_alias(agg_key, default="count"))
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                if col not in crash_col_map and fn == "count":
                    alias = alias_override or _safe_sql_alias(agg_key or col, default="count")
                    agg_selects.append(f"COUNT(*) AS {alias}")
                    agg_aliases.append(alias)
                    agg_alias_map[agg_key] = alias
                    agg_alias_map[col] = alias
                    continue
                expr = crash_col_map[col]
                if fn in ("mean", "avg"):
                    alias = alias_override or f"{col}_avg"
                    agg_selects.append(f"AVG({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "sum":
                    alias = alias_override or f"{col}_sum"
                    agg_selects.append(f"SUM({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "min":
                    alias = alias_override or f"{col}_min"
                    agg_selects.append(f"MIN({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "max":
                    alias = alias_override or f"{col}_max"
                    agg_selects.append(f"MAX({expr}) AS {alias}")
                    agg_aliases.append(alias)
                elif fn == "count":
                    alias = alias_override or f"{col}_count"
                    agg_selects.append(f"COUNT({expr}) AS {alias}")
                    agg_aliases.append(alias)
                else:
                    raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL crash mode")
                agg_alias_map[agg_key] = alias
                agg_alias_map[col] = alias

            order_clause = ""
            if sort_cols:
                order_parts = []
                for col, asc in zip(sort_cols, sort_dirs):
                    resolved_sort_col = _resolve_queryable_source_column(col, queryable_alias_map) or col
                    if col in agg_aliases:
                        order_expr = col
                    elif resolved_sort_col in agg_aliases:
                        order_expr = resolved_sort_col
                    elif col in agg_alias_map:
                        order_expr = agg_alias_map[col]
                    elif resolved_sort_col in agg_alias_map:
                        order_expr = agg_alias_map[resolved_sort_col]
                    else:
                        raise ValueError(f"Unsupported sort column '{col}' for aggregate result")
                    order_parts.append(f"{order_expr} {'ASC' if asc else 'DESC'}")
                order_clause = f" ORDER BY {', '.join(order_parts)}"

            limit_clause = ""
            if head_req is not None:
                limit_clause = " LIMIT %s"
                where_params = where_params + [int(head_req)]

            sql = f"""
                SELECT {", ".join(agg_selects)}
                FROM {from_sql}
                WHERE {where_sql}
                {order_clause}
                {limit_clause}
            """
        else:
            if map_req is not None and head_req is None:
                if map_rows_plotted is None:
                    sql = f"""
                        SELECT COUNT(*) AS count
                        FROM {from_sql}
                        WHERE {where_sql}
                    """
                else:
                    sql = "SELECT %s::int AS count"
                    where_params = [int(map_rows_plotted)]
            else:
                limit = head_req or 20
                order_clause = ""
                if sort_cols:
                    order_parts = []
                    for col, asc in zip(sort_cols, sort_dirs):
                        source_col = _require_queryable(col)
                        if source_col not in crash_col_map:
                            _ensure_event_col(source_col, numeric=False)
                        if source_col not in crash_col_map:
                            suggestions = _get_column_suggestions(source_col, schema_cols)
                            raise ValueError(f"Unsupported sort column '{col}' in SQL crash mode.{suggestions}")
                        order_parts.append(f"{crash_col_map[source_col]} {'ASC' if asc else 'DESC'}")
                    order_clause = f" ORDER BY {', '.join(order_parts)}"

                select_specs: list[tuple[str, str]] = []
                seen_aliases: set[str] = set()
                for field in queryable_policy.get("fields") or []:
                    if not isinstance(field, dict):
                        continue
                    if not bool(field.get("enabled", True)):
                        continue
                    alias = str(field.get("query_name") or "").strip()
                    source_col = str(field.get("source_column") or "").strip()
                    if not alias or alias in seen_aliases or not source_col:
                        continue
                    if source_col not in crash_col_map:
                        _ensure_event_col(source_col, numeric=False)
                    if source_col not in crash_col_map:
                        continue
                    select_specs.append((alias, crash_col_map[source_col]))
                    seen_aliases.add(alias)

                for _cond in _iter_leaf_conditions(filters):
                    source_col = (_cond.get("column") or "").strip()
                    if not source_col or source_col not in crash_col_map or source_col in seen_aliases:
                        continue
                    select_specs.append((source_col, crash_col_map[source_col]))
                    seen_aliases.add(source_col)

                if not select_specs:
                    raise QueryablePolicyError(
                        "No enabled queryable fields are currently available for tabular output.\n"
                        "Open Ingestion > Queryable Fields, enable at least one valid field, save, and retry."
                    )

                select_clause = ",\n                    ".join(
                    f"{expr} AS {alias}"
                    for alias, expr in select_specs
                )
                sql = f"""
                    SELECT
                    {select_clause}
                    FROM {from_sql}
                    WHERE {where_sql}
                    {order_clause}
                    LIMIT %s
                """
                where_params = where_params + [limit]

        log.info(json.dumps({
            "event": "sql_compiled",
            "dataset": dataset,
            "dataset_id": dataset_id,
            "sql": sql.strip()[:2500],
            "params": where_params
        }, default=str))

        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, where_params)
            cols = [desc[0] for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)

        log.info(json.dumps({
            "event": "sql_result",
            "rows": int(len(df)),
            "cols": list(df.columns)[:50]
        }, default=str))

        # Translate coded values to human-readable labels (static + codebook)
        for col in df.columns:
            if has_code_lookup(col):
                df[col] = df[col].apply(lambda x: translate_code_value(col, x) if pd.notna(x) else x)
        df = _apply_codebook_labels(df, dataset_id=dataset_id)

        is_map_only_count = (
            map_req is not None
            and groupby_req is None
            and aggregate_req is None
            and unique_values_req is None
            and (head_req is None)
        )
        wants_chart = False if is_map_only_count else _query_requests_chart(reasoning, steps)
        chart_payload: list[dict[str, Any]] = []
        if groupby_req and isinstance(groupby_req, dict):
            group_cols = resolved_group_cols_for_chart or (groupby_req.get("group_by") or [])
            aggs = groupby_req.get("aggregations") or {}
            if wants_chart or (len(group_cols) == 1 and _has_count_aggregation(aggs)):
                chart = _build_groupby_bar_chart_payload(
                    df=df,
                    group_cols=group_cols,
                    dataset_id=dataset_id,
                    reasoning=reasoning,
                    chart_role="crash_groupby_distribution",
                )
                if chart:
                    chart_payload.append(chart)
            if not chart_payload and wants_chart:
                auto_chart = _build_auto_chart_payload_from_df(
                    df=df,
                    reasoning=reasoning,
                    steps=steps,
                    chart_role="crash_query_result",
                    dataset_id=dataset_id,
                    group_cols=group_cols,
                )
                if auto_chart:
                    chart_payload.append(auto_chart)
        elif wants_chart:
            auto_chart = _build_auto_chart_payload_from_df(
                df=df,
                reasoning=reasoning,
                steps=steps,
                chart_role="crash_query_result",
                dataset_id=dataset_id,
                group_cols=None,
            )
            if auto_chart:
                chart_payload.append(auto_chart)

        if is_map_only_count and chart_payload:
            log.info(
                json.dumps(
                    {
                        "event": "map_only_chart_suppressed",
                        "dataset": dataset,
                        "dataset_id": dataset_id,
                        "chart_count": len(chart_payload),
                    },
                    default=str,
                )
            )
            chart_payload = []

        if chart_payload:
            _publish_chart_payload(chart_payload, label="Crash analysis chart")

        response_parts = ["REPORT FROM CRASH (SQL) SPECIALIST:\n"]
        if reasoning:
            response_parts.append(f"Analysis Plan: {reasoning}\n")
        response_parts.append(f"Crash dataset_id used: {dataset_id}")
        if filters:
            response_parts.append(f"Filters applied: {filters}")
        if map_req is not None:
            response_parts.append(f"Map generated with limit={int(map_req.get('limit', 5000))}")
        if chart_payload:
            response_parts.append("Visualization generated in panel.")

        response_parts.append("\nFINAL DATA RESULTS:")
        if df.empty:
            response_parts.append("Result is an empty table (No data found).")
        elif is_map_only_count and ("count" in df.columns):
            try:
                count_value = int(df.iloc[0]["count"])
            except Exception:
                count_value = df.iloc[0]["count"]
            response_parts.append(f"Mapped crash points: {count_value}")
        elif wants_chart and chart_payload:
            response_parts.append(f"Returned {len(df)} rows for the chart.")
        else:
            response_parts.append(_df_to_markdown_safe(df))

        return "\n".join(response_parts)

    except QueryablePolicyError as e:
        msg = f"[QUERYABLE_POLICY_BLOCKED]\n{str(e)}"
        print(msg)
        return msg
    except Exception as e:
        error_msg = f"❌ SQL MODE ERROR: {str(e)}\nTraceback:\n{traceback.format_exc()}"
        print(error_msg)
        return error_msg

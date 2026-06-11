"""Traffic SQL filter/planner helpers."""

from __future__ import annotations

from .common import Any, List, NON_DRIVABLE_HINT_TERMS, Optional, re, _resolve_sql_column_key

def _collect_road_filter_values(conditions: list[dict], out: list[tuple[str, Any]]) -> None:
    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        if "conditions" in cond:
            _collect_road_filter_values(cond.get("conditions") or [], out)
            continue
        col = (cond.get("column") or "").strip()
        if col not in ("road", "road_name", "name"):
            continue
        op = (cond.get("operator") or "").strip().lower()
        out.append((op, cond.get("value")))


def _parse_agg_specs(
    aggs: dict,
    *,
    default_col_map: dict,
    allowed_col_map: Optional[dict] = None,
) -> tuple[list[tuple[str, str, str]], dict]:
    """
    Returns:
      - list of (column, fn, alias)
      - sort alias map: alias_variant -> primary alias
    """
    specs: list[tuple[str, str, str]] = []
    alias_map: dict[str, str] = {}
    active_col_map = allowed_col_map or default_col_map

    def _alias_variants(col: Optional[str], fn: str, primary: str) -> set[str]:
        variants = {primary}
        if col:
            col_clean = col
            if fn in ("mean", "avg"):
                variants.update({
                    f"{col_clean}_avg",
                    f"{col_clean}_mean",
                    f"avg_{col_clean}",
                    f"mean_{col_clean}",
                    col_clean,
                })
            elif fn == "sum":
                variants.update({f"{col_clean}_sum", f"sum_{col_clean}", col_clean})
            elif fn == "min":
                variants.update({f"{col_clean}_min", f"min_{col_clean}", col_clean})
            elif fn == "max":
                variants.update({f"{col_clean}_max", f"max_{col_clean}", col_clean})
            elif fn == "count":
                variants.update({f"{col_clean}_count", f"count_{col_clean}", col_clean})
        else:
            variants.add("count")
        return variants

    def _parse_alias_key(key: str) -> tuple[Optional[str], Optional[str]]:
        if not key:
            return None, None
        lowered = key.lower()
        prefix_map = {
            "avg_": "avg",
            "mean_": "avg",
            "sum_": "sum",
            "total_": "sum",
            "min_": "min",
            "max_": "max",
            "count_": "count",
            "num_": "count",
            "n_": "count",
        }
        suffix_map = {
            "_avg": "avg",
            "_mean": "avg",
            "_sum": "sum",
            "_total": "sum",
            "_min": "min",
            "_max": "max",
            "_count": "count",
            "_num": "count",
        }
        for prefix, fn in prefix_map.items():
            if lowered.startswith(prefix) and len(key) > len(prefix):
                return key[len(prefix):], fn
        for suffix, fn in suffix_map.items():
            if lowered.endswith(suffix) and len(key) > len(suffix):
                return key[:-len(suffix)], fn
        return None, None

    for raw_key, raw_val in (aggs or {}).items():
        alias = None
        col = None
        fn = None

        if isinstance(raw_val, dict):
            fn = (raw_val.get("fn") or raw_val.get("function") or raw_val.get("agg") or "").lower() or None
            col = raw_val.get("column") or raw_val.get("col")
            alias = raw_val.get("alias") or raw_key
        else:
            fn = (raw_val or "").lower() if isinstance(raw_val, str) else None
            # Accept function-call strings: "mean(speed_over_limit)", "count(*)", etc.
            if isinstance(fn, str):
                m = re.match(r"^\s*([a-z_]+)\s*\(\s*([a-zA-Z0-9_*]+)\s*\)\s*$", fn)
                if m:
                    fn = m.group(1).strip().lower()
                    inner_col = m.group(2).strip()
                    if inner_col != "*" and not col:
                        col = inner_col

        if raw_key == "count" or fn == "count":
            # Any alias with fn=count (e.g. "record_count", "volume", "n_rows") -> COUNT(*)
            col = "count"
            fn = "count"
        elif raw_key in active_col_map:
            col = col or raw_key
        elif not col:
            # col not set from dict — try to infer from the alias key itself
            parsed_col, parsed_fn = _parse_alias_key(raw_key)
            if parsed_col and parsed_fn:
                col = parsed_col
                fn = fn or parsed_fn
                alias = alias or raw_key

        # col="*" always means COUNT(*).
        if col == "*":
            col = "count"

        # _resolve_sql_column_key can map volume proxies (point_count, traffic_volume, etc.) to "count"
        if col and col != "count":
            resolved_col = _resolve_sql_column_key(col, active_col_map)
            if resolved_col:
                col = resolved_col

        if col == "count":
            fn = "count"  # Count sentinel always uses count regardless of requested fn.
            primary_alias = alias or "count"
        else:
            if col not in active_col_map:
                raise ValueError(f"Unsupported aggregation column '{col}' in SQL mode")
            if fn is None:
                raise ValueError(f"Missing aggregation fn for '{raw_key}' in SQL mode")
            if fn in ("mean", "avg"):
                primary_alias = alias or f"{col}_avg"
            elif fn == "sum":
                primary_alias = alias or f"{col}_sum"
            elif fn == "min":
                primary_alias = alias or f"{col}_min"
            elif fn == "max":
                primary_alias = alias or f"{col}_max"
            elif fn == "count":
                primary_alias = alias or f"{col}_count"
            else:
                raise ValueError(f"Unsupported aggregation fn '{fn}' in SQL mode")

        specs.append((col, fn, primary_alias))
        for variant in _alias_variants(None if col == "count" else col, fn, primary_alias):
            alias_map[variant] = primary_alias

    return specs, alias_map


def _iter_filter_conditions(conditions: list[dict]) -> List[dict]:
    out: List[dict] = []
    for cond in conditions or []:
        if not isinstance(cond, dict):
            continue
        nested = cond.get("conditions")
        if isinstance(nested, list):
            out.extend(_iter_filter_conditions(nested))
        else:
            out.append(cond)
    return out


def _filters_request_non_drivable_roads(conditions: list[dict]) -> bool:
    for cond in _iter_filter_conditions(conditions):
        col_norm = re.sub(r"[^a-z0-9]+", "", str(cond.get("column") or "").lower())
        if col_norm not in {"road", "roadname", "name", "label", "highway", "ref"}:
            continue
        value_text = str(cond.get("value") or "").lower()
        if any(term in value_text for term in NON_DRIVABLE_HINT_TERMS):
            return True
    return False


def _collect_road_filters(conditions: list[dict], out: dict) -> None:
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        if "conditions" in cond:
            _collect_road_filters(cond.get("conditions") or [], out)
            continue
        col = cond.get("column")
        op = (cond.get("operator") or "").lower()
        val = cond.get("value")
        if col == "road_segment_id":
            if op in ("==", "=") and val:
                out["road_segment_id"] = str(val)
            elif op == "in" and isinstance(val, (list, tuple)) and val:
                out["road_segment_id"] = str(val[0])
        elif col == "way_id":
            if op in ("==", "=") and val:
                out["road_segment_id"] = str(val)
            elif op == "in" and isinstance(val, (list, tuple)) and val:
                out["road_segment_id"] = str(val[0])
        elif col in ("road", "road_name", "name"):
            if op in ("contains", "==", "=", "ilike", "like") and val:
                out["road_name"] = str(val)


def _is_speed_over_limit_expression(raw: Any) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", text)
    candidates = {
        "speedmeanspeedlimitmean",
        "meanspeedmeanspeedlimitmean",
        "avgspeedavgspeedlimit",
        "avgspeedspeedlimitavg",
        "speedavgspeedlimitavg",
        "speedmeanspeedlimitavg",
        "speedmean-speedlimitmean",
    }
    if compact in candidates:
        return True
    return (
        ("speed" in compact)
        and ("limit" in compact)
        and ("mean" in compact or "avg" in compact)
        and ("-" in text or "minus" in text)
    )


def _normalize_derived_metric_references(
    filters: list[dict],
    sort_req: Optional[dict],
    groupby_req: Optional[dict],
) -> None:
    derived_used = False
    speed_aliases = {"speedmean", "speedavg", "avgspeed", "meanspeed"}
    limit_aliases = {
        "speedlimit",
        "speedlimitmph",
        "speedlimitmean",
        "speedlimitavg",
        "avgspeedlimit",
        "meanspeedlimit",
        "speedlimitmphmean",
        "speedlimitmphavg",
    }

    def _norm_metric_name(raw: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(raw or "").strip().lower())

    def _walk_filter_conditions(conditions: list[dict]) -> None:
        nonlocal derived_used
        for cond in conditions or []:
            if not isinstance(cond, dict):
                continue
            nested = cond.get("conditions")
            if isinstance(nested, list):
                _walk_filter_conditions(nested)
                continue
            col = cond.get("column")
            col_norm = _norm_metric_name(col)

            # Rewrite pseudo-aggregate aliases to supported base columns.
            if col_norm in speed_aliases:
                cond["column"] = "speed"
                col = "speed"
                col_norm = "speed"
            elif col_norm in limit_aliases:
                cond["column"] = "SpeedLimitMPH"
                col = "SpeedLimitMPH"
                col_norm = "speedlimitmph"

            # Rewrite expression-style comparison:
            # speed_mean >= SpeedLimit_mean + N  -> speed_over_limit >= N
            # speed > {column: SpeedLimitMPH, add: N} -> speed_over_limit > N
            val = cond.get("value")
            if col_norm in {"speed", "avgspeed", "meanspeed"} and isinstance(val, dict):
                rhs_col_norm = _norm_metric_name(val.get("column"))
                rhs_op = str(val.get("operator") or "").strip()
                rhs_num = val.get("value")
                rhs_add = val.get("add")
                if rhs_add is None:
                    rhs_add = val.get("offset")
                if rhs_add is None:
                    rhs_add = val.get("delta")
                if rhs_add is None:
                    rhs_add = val.get("plus")
                if rhs_col_norm in limit_aliases and (rhs_op == "+" or rhs_add is not None):
                    try:
                        threshold = float(rhs_add if rhs_add is not None else rhs_num)
                    except (TypeError, ValueError):
                        threshold = None
                    if threshold is not None:
                        cond["column"] = "speed_over_limit"
                        cond["value"] = threshold
                        derived_used = True
                        continue

            if _is_speed_over_limit_expression(col):
                cond["column"] = "speed_over_limit"
                derived_used = True

    _walk_filter_conditions(filters)

    if isinstance(sort_req, dict):
        by = sort_req.get("by")
        if isinstance(by, list):
            normalized: list[Any] = []
            for col in by:
                if _is_speed_over_limit_expression(col):
                    normalized.append("speed_over_limit")
                    derived_used = True
                else:
                    normalized.append(col)
            sort_req["by"] = normalized
        elif _is_speed_over_limit_expression(by):
            sort_req["by"] = "speed_over_limit"
            derived_used = True

    # If a derived speed-over-limit metric was referenced, ensure groupby includes it
    # so sort alias resolution stays valid in optimized and fallback paths.
    if derived_used and isinstance(groupby_req, dict):
        aggs = groupby_req.get("aggregations")
        if isinstance(aggs, dict) and "speed_over_limit" not in aggs:
            aggs["speed_over_limit"] = "mean"


def _normalize_group_cols(params: dict) -> list[str]:
    cols = (
        params.get("group_by")
        or params.get("group_by_columns")
        or params.get("group_by_cols")
        or params.get("groupBy")
        or []
    )
    if isinstance(cols, str):
        cols = [cols]
    return list(cols or [])


def _normalize_aggs(params: dict) -> dict:
    """
    Canonical format expected by _parse_agg_specs:
      {"traffic_volume": "sum"}
      {"total_traffic_volume": {"fn": "sum", "column": "traffic_volume", "alias": "total_traffic_volume"}}
    """
    aggs = params.get("aggregations") or params.get("aggregation") or {}

    # If already a dict, keep it.
    if isinstance(aggs, dict):
        return aggs

    # If LLM emitted list-of-objects, convert to dict form.
    if isinstance(aggs, list):
        out = {}
        for a in aggs:
            if not isinstance(a, dict):
                continue
            col = a.get("column") or a.get("aggregation_column")
            fn = (a.get("fn") or a.get("type") or a.get("aggregation_type") or "").lower().strip()
            alias = a.get("alias") or a.get("new_column_name")

            # Choose dict key:
            key = alias or col or "count"

            # Build spec:
            if key == "count" and (col in (None, "*", "count") or fn == "count"):
                out["count"] = "count"
            else:
                spec = {"fn": fn or "sum"}
                if col:
                    spec["column"] = col
                if alias:
                    spec["alias"] = alias
                out[key] = spec
        return out

    return {}




def _parse_traffic_sql_steps(steps: list[dict]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "filters": [],
        "filter_mode": "and",
        "having_conditions": [],
        "having_mode": "and",
        "map_req": None,
        "groupby_req": None,
        "aggregate_req": None,
        "unique_values_req": None,
        "sort_req": None,
        "head_req": None,
        "top_speed_req": None,
        "top_hard_braking_req": None,
    }
    # normalize keys/types
    # Track whether we've seen a groupby/aggregate so subsequent filters become HAVING clauses.
    seen_groupby_or_aggregate = False

    for step in steps:
        op = (step.get("operation") or "").lower()
        params = step.get("params", {}) or {}
        if op == "filter":
            conditions = params.get("conditions", []) or []
            mode = params.get("mode", "and") or "and"
            if seen_groupby_or_aggregate:
                # A filter step after groupby/aggregate is a HAVING clause.
                state["having_conditions"].extend(conditions)
                state["having_mode"] = mode
            else:
                state["filters"].extend(conditions)
                state["filter_mode"] = mode or state["filter_mode"]
        elif op == "having":
            # Explicit having step (from updated planner prompt).
            conditions = params.get("conditions", []) or []
            mode = params.get("mode", "and") or "and"
            state["having_conditions"].extend(conditions)
            state["having_mode"] = mode
        elif op == "generate_map":
            state["map_req"] = params
        elif op == "groupby":
            group_cols = _normalize_group_cols(params)
            aggs = _normalize_aggs(params)
            state["groupby_req"] = {"group_by": group_cols, "aggregations": aggs}
            seen_groupby_or_aggregate = True
        elif op == "aggregate":
            aggs = _normalize_aggs(params)
            state["aggregate_req"] = {"aggregations": aggs}
            seen_groupby_or_aggregate = True
        elif op == "head":
            n = params.get("n", None)
            if n is None:
                n = params.get("count", 10)
            state["head_req"] = int(n)
        elif op == "unique_values":
            state["unique_values_req"] = params
        elif op == "sort":
            state["sort_req"] = params
        elif op == "top_speed_roads":
            state["top_speed_req"] = params
        elif op == "top_hard_braking_roads":
            state["top_hard_braking_req"] = params
        else:
            raise ValueError(
                f"SQL mode does not support operation '{op}' yet. "
                "Supported: filter, having, generate_map, groupby, aggregate, unique_values, sort, head "
                "(legacy: top_speed_roads, top_hard_braking_roads)."
            )
    return state

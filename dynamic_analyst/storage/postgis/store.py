# dynamic_analyst/storage/postgis/store.py
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import psycopg2.extras

from ...session_state import get_active_session, get_active_user
from ...config import CRASH_TIMEZONE
from .db_pool import get_db_connection
from . import detection as _detection
from . import datasets as _datasets
from . import events as _events
from . import roads as _roads
from . import schema as _schema
from .roads_utils import _detect_road_fields
from .table_names import APP_DATASET_CODEBOOK, APP_DATASETS, APP_EVENTS

logger = logging.getLogger("adk_server")

# Re-exported for backwards compatibility with existing imports/tests.
detect_codebook_schema = _detection.detect_codebook_schema
profile_columns = _detection.profile_columns
_looks_like_event_dataset = _detection._looks_like_event_dataset


def _conn():
    return get_db_connection()


def _sid() -> str:
    return get_active_session() or "_no_session_"


def _uid() -> str:
    return get_active_user() or "dev-user"


def _owner_filter():
    """Return (where_fragment, param) for user_id or session_id scoping."""
    uid = _uid()
    if uid:
        return "user_id = %s", uid
    return "session_id = %s", _sid()


def _events_upload_columns_exist(cur) -> bool:
    return _schema.events_upload_columns_exist(cur)


def _ensure_events_upload_columns(cur) -> None:
    _schema.ensure_events_upload_columns(cur)


def _ensure_core_tables(cur) -> None:
    _schema.ensure_core_tables(cur)


def _ensure_codebook_table() -> None:
    _schema.ensure_codebook_table(_conn)


def ingest_codebook_df(df: pd.DataFrame, source: str = "") -> Dict[str, Any]:
    schema = detect_codebook_schema(df)
    if not schema:
        raise ValueError("Not a recognized codebook file (missing attr/code/label columns).")

    _ensure_codebook_table()
    sid = _sid()
    uid = _uid()

    attr_col = schema["attr"]
    code_col = schema["code"]
    label_col = schema["label"]
    entity_col = schema.get("entity") or None

    rows = []
    seen_pairs: set[tuple[str, str]] = set()
    cols_needed = [attr_col, code_col, label_col] + ([entity_col] if entity_col else [])
    for rec in df[cols_needed].to_dict(orient="records"):
        attr_val = rec.get(attr_col)
        code_val = rec.get(code_col)
        label_val = rec.get(label_col)
        if attr_val is None or code_val is None:
            continue
        attr_text = str(attr_val).strip().upper()
        code_text = str(code_val).strip()
        if not attr_text or not code_text:
            continue
        key = (attr_text, code_text)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        label_text = str(label_val).strip() if label_val is not None else None
        entity_text = (
            str(rec.get(entity_col)).strip()
            if entity_col and rec.get(entity_col) is not None
            else None
        )
        rows.append((uid, sid, attr_text, code_text, label_text or None, entity_text, source))

    if not rows:
        return {"inserted": 0, "attributes": 0, "source": source}

    with _conn() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO """ + APP_DATASET_CODEBOOK + """(owner_user_id, session_id, attr, code, label, entity, source)
            VALUES %s
            ON CONFLICT (owner_user_id, attr, code)
            DO UPDATE SET session_id=EXCLUDED.session_id, label=EXCLUDED.label, entity=EXCLUDED.entity, source=EXCLUDED.source, updated_at=now()
            """,
            rows,
            page_size=5000,
        )

    return {
        "inserted": len(rows),
        "attributes": len({r[2] for r in rows}),
        "source": source,
    }


def list_codebook_attributes() -> set[str]:
    _ensure_codebook_table()
    uid = _uid()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT attr FROM {APP_DATASET_CODEBOOK} WHERE owner_user_id=%s",
            (uid,),
        )
        return {r[0] for r in cur.fetchall()}


_CODEBOOK_TOKEN_EXPANSIONS = {
    "COND": "CONDITION",
    "VEH": "VEHICLE",
    "INTRSC": "INTERSECTION",
    "LOC": "LOCATION",
    "DIR": "DIRECTION",
    "SPD": "SPEED",
    "LMT": "LIMIT",
    "NO": "NUMBER",
}


def _normalize_code_mapping_name(name: str) -> str:
    text = str(name or "").strip().upper()
    if not text:
        return ""
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if re.search(r"_[0-9]+$", text):
        text = re.sub(r"_[0-9]+$", "", text)
    tokens = [tok for tok in text.split("_") if tok]
    expanded = [_CODEBOOK_TOKEN_EXPANSIONS.get(tok, tok) for tok in tokens]
    return "_".join(expanded)


def _looks_like_code_column(values: list[str], dtype_category: str, unique_count: int) -> bool:
    if unique_count <= 0 or unique_count > 60:
        return False
    if not values:
        return False
    lengths = [len(v) for v in values if v]
    if not lengths:
        return False
    avg_len = sum(lengths) / len(lengths)
    max_len = max(lengths)
    dtype = (dtype_category or "").lower()
    if dtype == "numeric":
        return True
    if avg_len <= 4:
        return True
    return unique_count <= 20 and max_len <= 10


def _collect_unique_code_values(series: pd.Series, max_values: int = 120) -> list[str]:
    if series is None:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for raw in series.dropna().tolist():
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
        if len(values) >= max_values:
            break
    return values


def _load_codebook_attr_maps() -> Dict[str, Dict[str, str]]:
    _ensure_codebook_table()
    uid = _uid()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT attr, code, label FROM {APP_DATASET_CODEBOOK} WHERE owner_user_id=%s",
            (uid,),
        )
        rows = cur.fetchall()
    mapping: Dict[str, Dict[str, str]] = {}
    for attr, code, label in rows:
        attr_text = str(attr).strip().upper() if attr is not None else ""
        code_text = str(code).strip() if code is not None else ""
        if not attr_text or not code_text:
            continue
        label_text = str(label).strip() if label is not None else ""
        if attr_text not in mapping:
            mapping[attr_text] = {}
        mapping[attr_text][code_text] = label_text or code_text
        normalized = code_text.lstrip("0") or "0"
        if normalized not in mapping[attr_text]:
            mapping[attr_text][normalized] = label_text or code_text
    return mapping


def _build_code_mapping_meta(
    values: list[str],
    attr: str,
    method: str,
    code_lookup: Dict[str, str],
) -> Dict[str, Any]:
    mapped_values = [v for v in values if v in code_lookup or (v.lstrip("0") or "0") in code_lookup]
    sample_pairs = []
    for code in mapped_values[:8]:
        key = code if code in code_lookup else (code.lstrip("0") or "0")
        sample_pairs.append({"code": code, "label": code_lookup.get(key) or code_lookup.get(code) or code})
    total = len(values)
    mapped = len(mapped_values)
    coverage = float(mapped / total) if total else 0.0
    base_conf = 1.0 if method in {"exact", "manual"} else 0.7
    confidence = min(1.0, round(base_conf * (0.5 + 0.5 * coverage), 3))
    return {
        "attr": attr,
        "method": method,
        "confidence": confidence,
        "unique_values": total,
        "mapped_unique_values": mapped,
        "coverage": round(coverage, 4),
        "sample_labels": sample_pairs,
        "sample_values": values[:8],
    }


def analyze_code_mappings_for_dataframe(
    df: pd.DataFrame,
    column_profiles: Optional[Dict[str, Any]] = None,
    existing_mappings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Detect coded columns and map them to codebook attributes.
    Priority: exact attribute match first, normalized fallback second.
    """
    profiles = column_profiles or {}
    attr_maps = _load_codebook_attr_maps()
    attrs = sorted(attr_maps.keys())
    result: Dict[str, Any] = {
        "available": bool(attrs),
        "attributes": len(attrs),
        "matches": [],
        "candidates": [],
        "missing": [],
        "mappings": {},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    if not isinstance(df, pd.DataFrame) or df.empty:
        return result

    norm_attr_index: Dict[str, list[str]] = {}
    for attr in attrs:
        normalized = _normalize_code_mapping_name(attr)
        if not normalized:
            continue
        norm_attr_index.setdefault(normalized, []).append(attr)

    manual_map: Dict[str, str] = {}
    for col, info in (existing_mappings or {}).items():
        attr = ""
        if isinstance(info, str):
            attr = info
        elif isinstance(info, dict):
            attr = str(info.get("attr") or "").strip()
        attr_upper = attr.upper()
        if col in df.columns and attr_upper in attr_maps:
            manual_map[col] = attr_upper

    candidates: set[str] = set()
    mappings: Dict[str, Dict[str, Any]] = {}

    for col in df.columns:
        series = df[col]
        values = _collect_unique_code_values(series)
        profile = profiles.get(col, {}) if isinstance(profiles, dict) else {}
        unique_count = int(profile.get("unique_count") or len(values))
        dtype_category = str(profile.get("dtype_category") or "")

        exact_attr = col.upper() if col.upper() in attr_maps else ""
        has_manual = col in manual_map
        is_candidate = has_manual or bool(exact_attr) or _looks_like_code_column(values, dtype_category, unique_count)
        if not is_candidate:
            continue
        candidates.add(col)

        method = ""
        chosen_attr = ""
        if has_manual:
            chosen_attr = manual_map[col]
            method = "manual"
        elif exact_attr:
            chosen_attr = exact_attr
            method = "exact"
        else:
            normalized_col = _normalize_code_mapping_name(col)
            attr_candidates = norm_attr_index.get(normalized_col, [])
            if len(attr_candidates) == 1:
                chosen_attr = attr_candidates[0]
                method = "normalized"
            elif len(attr_candidates) > 1:
                scored = []
                for attr in attr_candidates:
                    lookup = attr_maps.get(attr, {})
                    mapped = sum(1 for v in values if v in lookup or (v.lstrip("0") or "0") in lookup)
                    scored.append((mapped, attr))
                scored.sort(reverse=True)
                if scored and scored[0][0] > 0:
                    chosen_attr = scored[0][1]
                    method = "normalized"

        if not chosen_attr:
            continue
        lookup = attr_maps.get(chosen_attr, {})
        meta = _build_code_mapping_meta(values, chosen_attr, method, lookup)
        if meta["mapped_unique_values"] <= 0 and method != "manual":
            continue
        mappings[col] = meta

    matches = sorted(mappings.keys())
    missing = sorted(candidates - set(matches))
    result["matches"] = matches
    result["candidates"] = sorted(candidates)
    result["missing"] = missing
    result["mappings"] = mappings
    return result


def get_dataset_codebook_info(dataset_id: str) -> Dict[str, Any]:
    owner_where, owner_val = _owner_filter()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT stats FROM {APP_DATASETS} WHERE dataset_id=%s AND {owner_where}",
            (dataset_id, owner_val),
        )
        row = cur.fetchone()
    if not row:
        return {}
    stats = row.get("stats") or {}
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            stats = {}
    if not isinstance(stats, dict):
        return {}
    codebook = stats.get("codebook") or {}
    return codebook if isinstance(codebook, dict) else {}


def get_dataset_code_mappings(dataset_id: str) -> Dict[str, str]:
    codebook_info = get_dataset_codebook_info(dataset_id)
    mappings = codebook_info.get("mappings") if isinstance(codebook_info, dict) else None
    if not isinstance(mappings, dict):
        return {}
    out: Dict[str, str] = {}
    for col, info in mappings.items():
        attr = ""
        if isinstance(info, str):
            attr = info
        elif isinstance(info, dict):
            attr = str(info.get("attr") or "")
        attr = attr.strip().upper()
        if col and attr:
            out[str(col)] = attr
    return out


def get_dataset_distinct_values(dataset_id: str, column: str, limit: int = 120) -> list[str]:
    if not column or not re.match(r"^[A-Za-z0-9_]+$", column):
        return []
    owner_where, owner_val = _owner_filter()
    query = f"""
        SELECT DISTINCT NULLIF(TRIM(e.props->>'{column}'), '') AS val
        FROM {APP_EVENTS} e
        WHERE e.dataset_id=%s AND {owner_where}
          AND NULLIF(TRIM(e.props->>'{column}'), '') IS NOT NULL
        LIMIT %s
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(query, (dataset_id, owner_val, int(limit)))
        rows = cur.fetchall()
    values = []
    for row in rows:
        val = row[0]
        if val is None:
            continue
        text = str(val).strip()
        if text:
            values.append(text)
    return values


def _sample_dataset_props_df(dataset_id: str, limit: int = 5000) -> pd.DataFrame:
    owner_where, owner_val = _owner_filter()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT props
            FROM {APP_EVENTS}
            WHERE dataset_id=%s AND {owner_where}
            LIMIT %s
            """,
            (dataset_id, owner_val, int(limit)),
        )
        rows = cur.fetchall()
    records: list[Dict[str, Any]] = []
    for row in rows:
        props = row[0]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = None
        if isinstance(props, dict):
            records.append(props)
    return pd.DataFrame(records) if records else pd.DataFrame()


def refresh_dataset_codebook_info(dataset_id: str, sample_rows: int = 5000) -> Dict[str, Any]:
    """
    Recompute codebook mappings for a dataset from sampled event props and persist in datasets.stats.codebook.
    """
    owner_where, owner_val = _owner_filter()
    current = get_dataset_codebook_info(dataset_id)
    existing_mappings = current.get("mappings") if isinstance(current.get("mappings"), dict) else {}

    df = _sample_dataset_props_df(dataset_id, limit=sample_rows)
    column_data = get_dataset_columns(dataset_id)
    profiles = column_data.get("column_profiles", {}) if isinstance(column_data, dict) else {}
    codebook_info = analyze_code_mappings_for_dataframe(
        df,
        column_profiles=profiles,
        existing_mappings=existing_mappings,
    )

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {APP_DATASETS}
            SET stats = COALESCE(stats,'{{}}'::jsonb) || %s::jsonb
            WHERE dataset_id=%s AND {owner_where}
            """,
            (json.dumps({"codebook": codebook_info}), dataset_id, owner_val),
        )
    return codebook_info


def refresh_session_codebook_info(sample_rows: int = 5000, max_datasets: Optional[int] = None) -> Dict[str, Any]:
    """
    Recompute codebook mappings for all ready event-like datasets in the current session.
    """
    refreshed = 0
    failed = 0
    details: Dict[str, Any] = {}
    processed = 0
    for ds in list_datasets():
        dataset_id = ds.get("dataset_id")
        if not dataset_id:
            continue
        if ds.get("status") != "ready":
            continue
        entity = (ds.get("entity_type") or "").lower()
        if entity not in {"crash", "event", "workzone", "signal"}:
            continue
        if max_datasets is not None and processed >= max(0, int(max_datasets)):
            break
        try:
            info = refresh_dataset_codebook_info(dataset_id, sample_rows=sample_rows)
            refreshed += 1
            processed += 1
            details[dataset_id] = {
                "matches": info.get("matches", []),
                "missing": info.get("missing", []),
            }
        except Exception as e:
            failed += 1
            details[dataset_id] = {"error": str(e)}
    return {"refreshed": refreshed, "failed": failed, "details": details}


def get_codebook_map_for_columns(columns: list[str], dataset_id: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    if not columns:
        return {}
    attr_maps = _load_codebook_attr_maps()
    if not attr_maps:
        return {}

    dataset_map = get_dataset_code_mappings(dataset_id) if dataset_id else {}
    norm_attr_index: Dict[str, list[str]] = {}
    for attr in attr_maps.keys():
        normalized = _normalize_code_mapping_name(attr)
        if not normalized:
            continue
        norm_attr_index.setdefault(normalized, []).append(attr)

    mapping: Dict[str, Dict[str, str]] = {}
    for col in columns:
        attr = dataset_map.get(col)
        if not attr:
            exact_attr = col.upper()
            if exact_attr in attr_maps:
                attr = exact_attr
            else:
                normalized_col = _normalize_code_mapping_name(col)
                candidates = norm_attr_index.get(normalized_col, [])
                if len(candidates) == 1:
                    attr = candidates[0]
        lookup = attr_maps.get(attr) if attr else None
        if not lookup:
            continue
        mapping[col] = dict(lookup)
    return mapping


def get_dataset_columns(dataset_id: str) -> Dict[str, Any]:
    """
    Get column profiles for a dataset from the database.
    Returns the stored column_profiles or queries the events table to build them.
    """
    owner_where, owner_val = _owner_filter()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # First check if we have stored column profiles
        cur.execute(
            f"SELECT stats FROM {APP_DATASETS} WHERE dataset_id=%s AND {owner_where}",
            (dataset_id, owner_val),
        )
        row = cur.fetchone()
        if row and row.get("stats"):
            stats = row["stats"] if isinstance(row["stats"], dict) else json.loads(row["stats"])
            if "column_profiles" in stats:
                return stats

        # If not, query the events table to get column info from props
        cur.execute(
            f"""
            SELECT props FROM {APP_EVENTS}
            WHERE dataset_id=%s AND {owner_where}
            LIMIT 100
            """,
            (dataset_id, owner_val),
        )
        rows = cur.fetchall()
        if not rows:
            return {"column_profiles": {}, "total_columns": 0, "error": "No events found"}

        # Build profiles from sample props
        all_columns = {}
        for row in rows:
            props = row["props"] if isinstance(row["props"], dict) else json.loads(row["props"])
            for col, val in props.items():
                if col not in all_columns:
                    all_columns[col] = {"values": [], "types": set()}
                if val is not None:
                    all_columns[col]["values"].append(val)
                    all_columns[col]["types"].add(type(val).__name__)

        profiles = {}
        for col, info in all_columns.items():
            values = info["values"]
            types = info["types"]

            # Determine type category
            if types <= {"int", "float"}:
                dtype_category = "numeric"
            elif types <= {"bool"}:
                dtype_category = "boolean"
            else:
                dtype_category = "text"

            # Get unique values for categorical
            unique_values = None
            unique_count = len(set(str(v) for v in values))
            if dtype_category == "text" and 0 < unique_count <= 20:
                unique_values = sorted(list(set(str(v) for v in values)))[:20]

            profiles[col] = {
                "dtype_category": dtype_category,
                "unique_count": unique_count,
                "unique_values": unique_values,
                "sample_values": values[:3] if values else [],
                "suggested_uses": ["filter"] + (["group_by"] if unique_count <= 20 else []),
            }

        return {
            "column_profiles": profiles,
            "total_columns": len(profiles),
            "source": "reconstructed_from_events",
        }



def create_dataset(name: str, entity_type: str) -> Dict[str, Any]:
    return _datasets.create_dataset(
        name,
        entity_type,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        ensure_core_tables_fn=_ensure_core_tables,
    )


def mark_ready(dataset_id: str, stats: Dict[str, Any], provenance_step: Optional[Dict[str, Any]] = None) -> None:
    _datasets.mark_ready(
        dataset_id,
        stats,
        provenance_step,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        ensure_core_tables_fn=_ensure_core_tables,
    )


def list_datasets() -> List[Dict[str, Any]]:
    return _datasets.list_datasets(conn_factory=_conn, sid_fn=_sid, uid_fn=_uid)

def get_session_data_awareness() -> str:
    """
    Get a data awareness context for all datasets in the current session.
    This is used to inject into agent prompts so they know what data is available.
    """
    from ...column_intelligence import generate_data_awareness_context

    datasets = list_datasets()
    if not datasets:
        datasets = []

    contexts = []
    for ds in datasets:
        if ds.get("status") != "ready":
            continue

        stats = ds.get("stats", {})
        if isinstance(stats, str):
            stats = json.loads(stats)

        ingest = stats.get("ingest", {})
        column_profiles = ingest.get("column_profiles", {})
        columns = set(column_profiles.keys()) if column_profiles else set()

        # If no column profiles, try to get columns from schema
        if not columns:
            col_data = get_dataset_columns(ds["dataset_id"])
            column_profiles = col_data.get("column_profiles", {})
            columns = set(column_profiles.keys())

        row_count = ingest.get("rows_inserted", 0)
        if not row_count:
            row_count = stats.get("row_count", 0)

        ctx = generate_data_awareness_context(
            dataset_id=ds["dataset_id"],
            dataset_name=ds.get("name", "unknown"),
            entity_type=ds.get("entity_type", "unknown"),
            row_count=row_count,
            columns=columns,
            column_profiles=column_profiles,
        )
        contexts.append(ctx)

    # Fallback: CV data in cv_points may not be registered in datasets
    has_cv = any((ds.get("entity_type") == "cv") for ds in datasets)
    if not has_cv:
        try:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT dataset_id FROM cv_points ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    cv_id = row[0]
                    cur.execute("SELECT COUNT(*) FROM cv_points WHERE dataset_id=%s", (cv_id,))
                    cv_count = int(cur.fetchone()[0])
                    cv_columns = {
                        "lat", "lon", "ts", "road_segment_id",
                        "speed", "SpeedLimitMPH", "speed_over_limit",
                        "acc_x", "acc_y", "road",
                    }
                    ctx = generate_data_awareness_context(
                        dataset_id=cv_id,
                        dataset_name="traffic",
                        entity_type="cv",
                        row_count=cv_count,
                        columns=cv_columns,
                        column_profiles=None,
                    )
                    contexts.append(ctx)
        except Exception:
            pass

    return "\n\n".join(contexts) if contexts else "No ready datasets in this session."


def get_dataset_awareness(dataset_id: str) -> str:
    """
    Get data awareness context for a specific dataset.
    """
    from ...column_intelligence import generate_data_awareness_context

    ds = get_dataset(dataset_id)
    stats = ds.get("stats", {})
    if isinstance(stats, str):
        stats = json.loads(stats)

    ingest = stats.get("ingest", {})
    column_profiles = ingest.get("column_profiles", {})
    columns = set(column_profiles.keys()) if column_profiles else set()

    if not columns:
        col_data = get_dataset_columns(dataset_id)
        column_profiles = col_data.get("column_profiles", {})
        columns = set(column_profiles.keys())

    row_count = ingest.get("rows_inserted", 0)

    return generate_data_awareness_context(
        dataset_id=dataset_id,
        dataset_name=ds.get("name", "unknown"),
        entity_type=ds.get("entity_type", "unknown"),
        row_count=row_count,
        columns=columns,
        column_profiles=column_profiles,
    )



def clear_session_data(session_id: str) -> Dict[str, Any]:
    return _datasets.clear_session_data(session_id, owner_user_id=_uid(), conn_factory=_conn)


def delete_dataset(dataset_id: str) -> Dict[str, Any]:
    return _datasets.delete_dataset(dataset_id, owner_user_id=_uid(), conn_factory=_conn)


def clear_all_data() -> Dict[str, Any]:
    return _datasets.clear_all_data(conn_factory=_conn)


def get_dataset(dataset_id: str) -> Dict[str, Any]:
    return _datasets.get_dataset(dataset_id, conn_factory=_conn, sid_fn=_sid, uid_fn=_uid)


def preview_events(dataset_id: str, limit: int = 6) -> List[Dict[str, Any]]:
    return _events.preview_events(
        dataset_id,
        limit,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        crash_timezone=CRASH_TIMEZONE,
    )


def ingest_events_df(dataset_id: str, df: pd.DataFrame) -> Dict[str, Any]:
    return _events.ingest_events_df(
        dataset_id,
        df,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        ensure_core_tables_fn=_ensure_core_tables,
        ensure_events_upload_columns_fn=_ensure_events_upload_columns,
        crash_timezone=CRASH_TIMEZONE,
    )


def map_events_to_roads(dataset_id: str, max_dist_m: float = 100.0, batch_size: int = 10_000) -> Dict[str, Any]:
    return _events.map_events_to_roads(
        dataset_id,
        max_dist_m,
        batch_size,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        ensure_core_tables_fn=_ensure_core_tables,
    )


def map_events_to_osm_ways(
    dataset_id: str,
    max_dist_m: float = 100.0,
    batch_size: int = 10_000,
    overwrite_existing: bool = False,
) -> Dict[str, Any]:
    return _events.map_events_to_osm_ways(
        dataset_id,
        max_dist_m,
        batch_size,
        overwrite_existing,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        ensure_core_tables_fn=_ensure_core_tables,
        ensure_events_upload_columns_fn=_ensure_events_upload_columns,
        logger_obj=logger,
    )


def get_events_bbox(buffer_degrees: float = 0.05) -> Optional[Dict[str, float]]:
    return _roads.get_events_bbox(buffer_degrees, conn_factory=_conn, sid_fn=_sid, uid_fn=_uid)


def clear_roads(session_only: bool = False) -> Dict[str, int]:
    return _roads.clear_roads(session_only=session_only, conn_factory=_conn)


def get_roads_count() -> int:
    return _roads.get_roads_count(conn_factory=_conn)


def ingest_roads(
    features: List[Dict],
    id_field: Optional[str] = None,
    name_field: Optional[str] = None,
    bbox_filter: Optional[Dict[str, float]] = None,
    clear_existing: bool = True,
    batch_size: int = 2000,
) -> Dict[str, Any]:
    return _roads.ingest_roads(
        features,
        id_field,
        name_field,
        bbox_filter,
        clear_existing,
        batch_size,
        conn_factory=_conn,
        detect_road_fields_fn=_detect_road_fields,
    )


def remap_events_to_roads(max_dist_m: float = 100.0) -> Dict[str, Any]:
    return _roads.remap_events_to_roads(
        max_dist_m,
        conn_factory=_conn,
        sid_fn=_sid,
        uid_fn=_uid,
        list_datasets_fn=list_datasets,
        map_events_to_roads_fn=map_events_to_roads,
    )

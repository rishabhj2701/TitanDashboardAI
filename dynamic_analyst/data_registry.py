import math
import re
import json
from typing import Dict, Any, List, Optional
import pandas as pd
import psycopg2.extras

from .session_state import get_active_session, get_active_user
from .storage.postgis.db_pool import get_db_connection
from .storage.postgis.table_names import APP_DATASETS


def _safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return str(value)


def _db_conn():
    return get_db_connection()


def _sid() -> str:
    return get_active_session() or "_no_session_"


def _uid() -> str:
    return get_active_user() or "dev-user"


_DATASET_ID_RE = re.compile(r"^[a-zA-Z]+_[0-9a-fA-F]{6,}$")


def _parse_stats(stats_val: Any) -> Dict[str, Any]:
    if not stats_val:
        return {}
    if isinstance(stats_val, dict):
        return stats_val
    try:
        return json.loads(stats_val)
    except Exception:
        return {}


class DatasetRegistry:
    """
    DB-only registry backed by PostGIS datasets/events tables.
    This class intentionally does NOT store DataFrames in memory.
    """

    def __init__(self):
        self.info: Dict[str, Dict[str, str]] = {}

    def _resolve_dataset_row(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        uid = _uid()
        if not name_or_id:
            return None

        with _db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if _DATASET_ID_RE.match(name_or_id):
                cur.execute(
                    f"SELECT * FROM {APP_DATASETS} WHERE dataset_id=%s AND owner_user_id=%s",
                    (name_or_id, uid),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)

            lowered = name_or_id.strip().lower()
            entity = None
            if lowered in {"crash", "crashes", "accident", "accidents"}:
                entity = "crash"
            elif lowered in {"traffic", "cv", "connected_vehicle", "connected-vehicle"}:
                entity = "cv"
            elif lowered in {"workzone", "workzones", "work_zone", "construction"}:
                entity = "workzone"

            if entity:
                cur.execute(
                    """
                    SELECT * FROM """ + APP_DATASETS + """
                    WHERE owner_user_id=%s AND status='ready' AND entity_type=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uid, entity),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)

            cur.execute(
                """
                SELECT * FROM """ + APP_DATASETS + """
                WHERE owner_user_id=%s AND lower(name)=lower(%s)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (uid, name_or_id),
            )
            row = cur.fetchone()
            if row:
                return dict(row)

        return None

    def resolve_dataset_name(self, name: str) -> Optional[str]:
        row = self._resolve_dataset_row(name)
        if row:
            return row.get("dataset_id")
        # fallback for CV: latest dataset_id seen in cv_points
        lowered = (name or "").strip().lower()
        if lowered in {"traffic", "cv", "connected_vehicle", "connected-vehicle"}:
            try:
                with _db_conn() as conn, conn.cursor() as cur:
                    cur.execute("SELECT dataset_id FROM cv_points ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        return row[0]
            except Exception:
                return None
        return None

    def list_datasets(self) -> List[str]:
        uid = _uid()
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT dataset_id FROM {APP_DATASETS} WHERE owner_user_id=%s ORDER BY created_at DESC",
                (uid,),
            )
            return [r[0] for r in cur.fetchall()]

    def get_metadata(self, name: str) -> Dict[str, Any]:
        row = self._resolve_dataset_row(name)
        if not row:
            raise ValueError(f"Unknown dataset '{name}'.")

        stats = _parse_stats(row.get("stats"))
        ingest = stats.get("ingest", {}) if isinstance(stats, dict) else {}
        column_profiles = ingest.get("column_profiles") or {}
        columns = list(column_profiles.keys()) if column_profiles else stats.get("columns") or []

        row_count = ingest.get("rows_inserted") or ingest.get("rows") or stats.get("row_count")

        meta = {
            "dataset_id": row.get("dataset_id"),
            "dataset": row.get("name"),
            "name": row.get("name"),
            "entity_type": row.get("entity_type"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "ingested_at": row.get("created_at"),
            "row_count": row_count,
            "columns": columns,
            "stats": stats,
            "geo": stats.get("geo", {}),
            "road_match": stats.get("road_match", {}),
            "capabilities": stats.get("capabilities", []),
            "aliases": stats.get("aliases", []),
            "source": stats.get("source"),
        }

        mapping = row.get("mapping")
        if mapping:
            meta["mapping"] = mapping
        else:
            mapping_stats = stats.get("mapping")
            if mapping_stats:
                meta["mapping"] = mapping_stats

        canonical = stats.get("canonical_dataset")
        if canonical:
            meta["canonical_dataset"] = canonical

        return meta

    def update_metadata(self, name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        row = self._resolve_dataset_row(name)
        if not row:
            raise ValueError(f"Unknown dataset '{name}'.")

        dataset_id = row.get("dataset_id")
        uid = _uid()
        updates = dict(updates or {})
        mapping = updates.pop("mapping", None)
        with _db_conn() as conn, conn.cursor() as cur:
            if mapping is not None:
                cur.execute(
                    f"UPDATE {APP_DATASETS} SET mapping=%s WHERE dataset_id=%s AND owner_user_id=%s",
                    (json.dumps(mapping), dataset_id, uid),
                )
            if updates:
                cur.execute(
                    """
                    UPDATE """ + APP_DATASETS + """
                    SET stats = COALESCE(stats,'{}'::jsonb) || %s::jsonb
                    WHERE dataset_id=%s AND owner_user_id=%s
                    """,
                    (json.dumps(updates), dataset_id, uid),
                )
        return self.get_metadata(dataset_id)

    def list_dataset_summaries(self) -> List[Dict[str, Any]]:
        uid = _uid()
        summaries: List[Dict[str, Any]] = []
        with _db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT dataset_id, name, entity_type, status, created_at, stats FROM {APP_DATASETS} WHERE owner_user_id=%s ORDER BY created_at DESC",
                (uid,),
            )
            rows = cur.fetchall()
        for row in rows:
            stats = _parse_stats(row.get("stats"))
            ingest = stats.get("ingest", {})
            column_profiles = ingest.get("column_profiles") or {}
            columns = list(column_profiles.keys()) if column_profiles else stats.get("columns") or []
            row_count = ingest.get("rows_inserted") or ingest.get("rows") or stats.get("row_count")
            summaries.append({
                "name": row.get("name"),
                "dataset_id": row.get("dataset_id"),
                "row_count": row_count,
                "columns": columns,
                "ingested_at": row.get("created_at"),
                "entity_type": row.get("entity_type"),
                "capabilities": stats.get("capabilities", []),
                "canonical_dataset": stats.get("canonical_dataset"),
                "aliases": stats.get("aliases", []),
                "geo": stats.get("geo", {}),
                "road_match": stats.get("road_match", {}),
                "source": stats.get("source"),
            })
        return summaries

    def get_all_info(self) -> str:
        sections: List[str] = []
        for meta in self.list_dataset_summaries():
            sections.append(f"\n{'='*60}")
            sections.append(f"{str(meta.get('name', '')).upper()} DATASET")
            sections.append('='*60)
            cols = meta.get("columns") or []
            sections.append(f"Columns: {cols}")
            sections.append(f"Row count: {meta.get('row_count')}")
        return "\n".join(sections)

    # Legacy in-memory hooks (disabled)
    def register(self, *args, **kwargs) -> None:
        raise ValueError("In-memory dataset registry is disabled. Use PostGIS ingestion instead.")

    def get_dataframe(self, *args, **kwargs):
        raise ValueError("In-memory dataset registry is disabled. Use PostGIS ingestion instead.")


registry = DatasetRegistry()

# try:
#     df_traffic = pd.read_csv(HERE / "cv_traffic.csv")
#     registry.register("traffic", df_traffic)
# except Exception as e:
#     print(f"Failed to load traffic data: {e}")

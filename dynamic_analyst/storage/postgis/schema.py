from __future__ import annotations

import threading

from psycopg2 import sql

from .table_names import (
    APP_DATASET_CODEBOOK,
    APP_DATASETS,
    APP_EVENTS,
    APP_SCHEMA,
    APP_USER_CV_RUN_CONFIG,
)


_SCHEMA_INIT_LOCK = threading.Lock()
_CORE_TABLES_READY = False
_EVENTS_UPLOAD_COLUMNS_READY = False
_LEGACY_BACKFILL_READY = False


def _rel(schema: str, table: str) -> str:
    return f"{schema}.{table}"


def _table_columns(cur, schema: str, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema, table),
    )
    return {str(r[0]).strip().lower() for r in cur.fetchall()}


def _relation_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (_rel(schema, table),))
    return cur.fetchone()[0] is not None


def _source_expr(cols: set[str], preferred: str, fallback: str | None = None, default: str = "NULL") -> sql.SQL:
    if preferred in cols:
        return sql.SQL("s.{}").format(sql.Identifier(preferred))
    if fallback and fallback in cols:
        return sql.SQL("s.{}").format(sql.Identifier(fallback))
    return sql.SQL(default)


def _backfill_legacy_tables(cur) -> None:
    global _LEGACY_BACKFILL_READY
    if _LEGACY_BACKFILL_READY:
        return
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.SQL(APP_DATASETS)))
    existing = int(cur.fetchone()[0] or 0)
    if existing > 0:
        _LEGACY_BACKFILL_READY = True
        return

    cur.execute(
        """
        SELECT DISTINCT table_schema
        FROM information_schema.tables
        WHERE table_name IN ('datasets', 'events', 'dataset_codebook')
          AND table_schema NOT IN ('pg_catalog', 'information_schema', 'topology', 'tiger', 'tiger_data')
        ORDER BY table_schema
        """
    )
    schemas = [str(r[0]).strip() for r in cur.fetchall() if str(r[0]).strip() and str(r[0]).strip() != APP_SCHEMA]

    for src_schema in schemas:
        try:
            if _relation_exists(cur, src_schema, "datasets"):
                dcols = _table_columns(cur, src_schema, "datasets")
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            dataset_id, owner_user_id, session_id, user_id, name, entity_type, status,
                            mapping, provenance, stats, created_at
                        )
                        SELECT
                            {dataset_id},
                            {owner_user_id},
                            {session_id},
                            {user_id},
                            {name},
                            {entity_type},
                            {status},
                            {mapping},
                            {provenance},
                            {stats},
                            {created_at}
                        FROM {} s
                        WHERE {dataset_id} IS NOT NULL
                        ON CONFLICT (dataset_id) DO NOTHING
                        """
                    ).format(
                        sql.SQL(APP_DATASETS),
                        sql.Identifier(src_schema, "datasets"),
                        dataset_id=_source_expr(dcols, "dataset_id"),
                        owner_user_id=_source_expr(dcols, "owner_user_id", fallback="user_id"),
                        session_id=_source_expr(dcols, "session_id", default="''"),
                        user_id=_source_expr(dcols, "user_id"),
                        name=_source_expr(dcols, "name", fallback="dataset_id", default="'unknown'"),
                        entity_type=_source_expr(dcols, "entity_type", default="'event'"),
                        status=_source_expr(dcols, "status", default="'ready'"),
                        mapping=_source_expr(dcols, "mapping", default="'{}'::jsonb"),
                        provenance=_source_expr(dcols, "provenance", default="'[]'::jsonb"),
                        stats=_source_expr(dcols, "stats", default="'{}'::jsonb"),
                        created_at=_source_expr(dcols, "created_at", default="NOW()"),
                    )
                )
        except Exception:
            continue

        try:
            if _relation_exists(cur, src_schema, "events"):
                ecols = _table_columns(cur, src_schema, "events")
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            dataset_id, owner_user_id, session_id, user_id,
                            ts, lat, lon, geom, geom_m, geom_feature, geom_feature_m,
                            road_segment_id, way_id, road_dist_m, road_conf, props
                        )
                        SELECT
                            {dataset_id},
                            {owner_user_id},
                            {session_id},
                            {user_id},
                            {ts},
                            {lat},
                            {lon},
                            {geom},
                            {geom_m},
                            {geom_feature},
                            {geom_feature_m},
                            {road_segment_id},
                            {way_id},
                            {road_dist_m},
                            {road_conf},
                            {props}
                        FROM {} s
                        WHERE {dataset_id} IS NOT NULL
                        """
                    ).format(
                        sql.SQL(APP_EVENTS),
                        sql.Identifier(src_schema, "events"),
                        dataset_id=_source_expr(ecols, "dataset_id"),
                        owner_user_id=_source_expr(ecols, "owner_user_id", fallback="user_id"),
                        session_id=_source_expr(ecols, "session_id", default="''"),
                        user_id=_source_expr(ecols, "user_id"),
                        ts=_source_expr(ecols, "ts"),
                        lat=_source_expr(ecols, "lat"),
                        lon=_source_expr(ecols, "lon"),
                        geom=_source_expr(ecols, "geom"),
                        geom_m=_source_expr(ecols, "geom_m"),
                        geom_feature=_source_expr(ecols, "geom_feature", fallback="geom"),
                        geom_feature_m=_source_expr(ecols, "geom_feature_m", fallback="geom_m"),
                        road_segment_id=_source_expr(ecols, "road_segment_id"),
                        way_id=_source_expr(ecols, "way_id"),
                        road_dist_m=_source_expr(ecols, "road_dist_m"),
                        road_conf=_source_expr(ecols, "road_conf"),
                        props=_source_expr(ecols, "props", default="'{}'::jsonb"),
                    )
                )
        except Exception:
            continue

        try:
            if _relation_exists(cur, src_schema, "dataset_codebook"):
                ccols = _table_columns(cur, src_schema, "dataset_codebook")
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            owner_user_id, session_id, attr, code, label, entity, source, updated_at
                        )
                        SELECT
                            {owner_user_id},
                            {session_id},
                            {attr},
                            {code},
                            {label},
                            {entity},
                            {source},
                            {updated_at}
                        FROM {} s
                        WHERE {attr} IS NOT NULL AND {code} IS NOT NULL
                        ON CONFLICT (owner_user_id, attr, code) DO NOTHING
                        """
                    ).format(
                        sql.SQL(APP_DATASET_CODEBOOK),
                        sql.Identifier(src_schema, "dataset_codebook"),
                        owner_user_id=_source_expr(ccols, "owner_user_id"),
                        session_id=_source_expr(ccols, "session_id", default="''"),
                        attr=_source_expr(ccols, "attr"),
                        code=_source_expr(ccols, "code"),
                        label=_source_expr(ccols, "label"),
                        entity=_source_expr(ccols, "entity"),
                        source=_source_expr(ccols, "source"),
                        updated_at=_source_expr(ccols, "updated_at", default="NOW()"),
                    )
                )
        except Exception:
            continue

    _LEGACY_BACKFILL_READY = True


def ensure_user_ownership_columns(cur) -> None:
    if _relation_exists(cur, APP_SCHEMA, "datasets"):
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS owner_user_id TEXT").format(sql.SQL(APP_DATASETS)))
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS datasets_owner_idx ON {} (owner_user_id)").format(sql.SQL(APP_DATASETS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS datasets_owner_created_idx ON {} (owner_user_id, created_at DESC)").format(
                sql.SQL(APP_DATASETS)
            )
        )

    if _relation_exists(cur, APP_SCHEMA, "events"):
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS owner_user_id TEXT").format(sql.SQL(APP_EVENTS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_owner_dataset_idx ON {} (owner_user_id, dataset_id)").format(
                sql.SQL(APP_EVENTS)
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_owner_session_idx ON {} (owner_user_id, session_id)").format(
                sql.SQL(APP_EVENTS)
            )
        )


def ensure_user_cv_run_config_table(cur) -> None:
    # ensure qualified app schema exists even when this is called outside core-table bootstrap
    cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(APP_SCHEMA)))
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                user_id TEXT PRIMARY KEY,
                active_run_id TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.SQL(APP_USER_CV_RUN_CONFIG))
    )
    cur.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS user_cv_run_config_active_idx ON {} (active_run_id)").format(
            sql.SQL(APP_USER_CV_RUN_CONFIG)
        )
    )


def events_upload_columns_exist(cur) -> bool:
    if not _relation_exists(cur, APP_SCHEMA, "events"):
        return False
    cur.execute(
        """
        SELECT attname
        FROM pg_attribute
        WHERE attrelid = to_regclass(%s)
          AND attnum > 0
          AND NOT attisdropped
          AND attname IN ('way_id', 'geom_feature', 'geom_feature_m')
        """,
        (_rel(APP_SCHEMA, "events"),),
    )
    cols = {str(row[0]).strip().lower() for row in cur.fetchall()}
    return {"way_id", "geom_feature", "geom_feature_m"}.issubset(cols)


def ensure_events_upload_columns(cur) -> None:
    global _EVENTS_UPLOAD_COLUMNS_READY
    if _EVENTS_UPLOAD_COLUMNS_READY:
        return
    if events_upload_columns_exist(cur):
        _EVENTS_UPLOAD_COLUMNS_READY = True
        return
    cur.execute("SET LOCAL lock_timeout = '2000ms'")
    cur.execute(
        sql.SQL(
            """
            ALTER TABLE {}
            ADD COLUMN IF NOT EXISTS way_id BIGINT,
            ADD COLUMN IF NOT EXISTS geom_feature geometry(Geometry, 4326),
            ADD COLUMN IF NOT EXISTS geom_feature_m geometry(Geometry, 26915)
            """
        ).format(sql.SQL(APP_EVENTS))
    )
    cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_way_id_idx ON {} (dataset_id, way_id)").format(sql.SQL(APP_EVENTS)))
    cur.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS events_geom_feature_m_gist ON {} USING GIST (geom_feature_m)").format(
            sql.SQL(APP_EVENTS)
        )
    )
    _EVENTS_UPLOAD_COLUMNS_READY = True


def ensure_core_tables(cur) -> None:
    global _CORE_TABLES_READY, _EVENTS_UPLOAD_COLUMNS_READY
    if _CORE_TABLES_READY:
        return
    with _SCHEMA_INIT_LOCK:
        if _CORE_TABLES_READY:
            return
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(APP_SCHEMA)))

        cur.execute(
            "SELECT to_regclass(%s), to_regclass(%s)",
            (_rel(APP_SCHEMA, "datasets"), _rel(APP_SCHEMA, "events")),
        )
        datasets_rel, events_rel = cur.fetchone()
        if datasets_rel is not None and events_rel is not None:
            ensure_user_ownership_columns(cur)
            ensure_user_cv_run_config_table(cur)
            _EVENTS_UPLOAD_COLUMNS_READY = events_upload_columns_exist(cur)
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS user_id TEXT").format(sql.SQL(APP_DATASETS)))
            cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS user_id TEXT").format(sql.SQL(APP_EVENTS)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS datasets_user_idx ON {} (user_id)").format(sql.SQL(APP_DATASETS)))
            cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_user_idx ON {} (user_id)").format(sql.SQL(APP_EVENTS)))
            ensure_events_upload_columns(cur)
            _backfill_legacy_tables(cur)
            _CORE_TABLES_READY = True
            return

        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                  dataset_id TEXT PRIMARY KEY,
                  owner_user_id TEXT,
                  session_id TEXT NOT NULL,
                  user_id TEXT,
                  name TEXT NOT NULL,
                  entity_type TEXT,
                  status TEXT NOT NULL DEFAULT 'ready',
                  mapping JSONB DEFAULT '{{}}'::jsonb,
                  provenance JSONB DEFAULT '[]'::jsonb,
                  stats JSONB DEFAULT '{{}}'::jsonb,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(sql.SQL(APP_DATASETS))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                  id BIGSERIAL PRIMARY KEY,
                  dataset_id TEXT NOT NULL,
                  owner_user_id TEXT,
                  session_id TEXT NOT NULL,
                  user_id TEXT,
                  ts TIMESTAMPTZ NULL,
                  lat DOUBLE PRECISION NULL,
                  lon DOUBLE PRECISION NULL,
                  geom geometry(Point, 4326),
                  geom_m geometry(Point, 26915),
                  geom_feature geometry(Geometry, 4326),
                  geom_feature_m geometry(Geometry, 26915),
                  road_segment_id TEXT NULL,
                  way_id BIGINT NULL,
                  road_dist_m DOUBLE PRECISION NULL,
                  road_conf DOUBLE PRECISION NULL,
                  props JSONB DEFAULT '{{}}'::jsonb NOT NULL
                )
                """
            ).format(sql.SQL(APP_EVENTS))
        )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS datasets_session_idx ON {} (session_id)").format(sql.SQL(APP_DATASETS)))
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS datasets_owner_idx ON {} (owner_user_id)").format(sql.SQL(APP_DATASETS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS datasets_owner_created_idx ON {} (owner_user_id, created_at DESC)").format(
                sql.SQL(APP_DATASETS)
            )
        )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_dataset_idx ON {} (dataset_id)").format(sql.SQL(APP_EVENTS)))
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_session_idx ON {} (session_id)").format(sql.SQL(APP_EVENTS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_owner_dataset_idx ON {} (owner_user_id, dataset_id)").format(
                sql.SQL(APP_EVENTS)
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_owner_session_idx ON {} (owner_user_id, session_id)").format(
                sql.SQL(APP_EVENTS)
            )
        )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_ts_idx ON {} (dataset_id, ts)").format(sql.SQL(APP_EVENTS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_road_idx ON {} (dataset_id, road_segment_id)").format(sql.SQL(APP_EVENTS))
        )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_way_id_idx ON {} (dataset_id, way_id)").format(sql.SQL(APP_EVENTS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_join_idx ON {} (dataset_id, road_segment_id, ts)").format(sql.SQL(APP_EVENTS))
        )
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_geom_m_gist ON {} USING GIST (geom_m)").format(sql.SQL(APP_EVENTS)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS events_geom_feature_m_gist ON {} USING GIST (geom_feature_m)").format(
                sql.SQL(APP_EVENTS)
            )
        )
        ensure_user_ownership_columns(cur)
        ensure_user_cv_run_config_table(cur)
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS datasets_user_idx ON {} (user_id)").format(sql.SQL(APP_DATASETS)))
        cur.execute(sql.SQL("CREATE INDEX IF NOT EXISTS events_user_idx ON {} (user_id)").format(sql.SQL(APP_EVENTS)))
        ensure_events_upload_columns(cur)
        _backfill_legacy_tables(cur)
        _CORE_TABLES_READY = True


def ensure_codebook_table(conn_factory) -> None:
    with conn_factory() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(APP_SCHEMA)))
        ensure_user_cv_run_config_table(cur)
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    owner_user_id text,
                    session_id text NOT NULL,
                    attr text NOT NULL,
                    code text NOT NULL,
                    label text,
                    entity text,
                    source text,
                    updated_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (session_id, attr, code)
                )
                """
            ).format(sql.SQL(APP_DATASET_CODEBOOK))
        )
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS owner_user_id text").format(sql.SQL(APP_DATASET_CODEBOOK)))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS dataset_codebook_owner_attr_idx ON {} (owner_user_id, attr)").format(
                sql.SQL(APP_DATASET_CODEBOOK)
            )
        )
        cur.execute(
            sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS dataset_codebook_owner_attr_code_uniq ON {} (owner_user_id, attr, code)").format(
                sql.SQL(APP_DATASET_CODEBOOK)
            )
        )

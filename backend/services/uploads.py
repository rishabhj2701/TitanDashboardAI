from __future__ import annotations

import asyncio
import ipaddress
import io
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import HTTPException, UploadFile

from backend.services.ingestion_entities import normalize_entity_type
from dynamic_analyst import postgis_store
from dynamic_analyst.column_intelligence import categorize_columns, generate_query_suggestions
from dynamic_analyst.geo_columns import GEO_LATITUDE_COLUMNS, GEO_LONGITUDE_COLUMNS
from dynamic_analyst.queryable_fields import resolve_queryable_fields
from dynamic_analyst.session_state import get_active_session


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    values = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    if not values:
        values = [part.strip().lower() for part in default.split(",") if part.strip()]
    return tuple(dict.fromkeys(values))


UPLOAD_MAX_BYTES = max(0, _env_int("UPLOAD_MAX_BYTES", 0))
UPLOAD_URL_ALLOW_PRIVATE_NET = _env_bool("UPLOAD_URL_ALLOW_PRIVATE_NET", True)
UPLOAD_URL_ALLOWED_SCHEMES = _env_csv("UPLOAD_URL_ALLOWED_SCHEMES", "http,https")
UPLOAD_URL_MAX_REDIRECTS = max(0, _env_int("UPLOAD_URL_MAX_REDIRECTS", 10))


def _log_permissive_upload_guardrails(logger: logging.Logger, source: str) -> None:
    if UPLOAD_MAX_BYTES <= 0:
        logger.warning("upload.guardrails.permissive source=%s setting=UPLOAD_MAX_BYTES value=0", source)
    if source == "url" and UPLOAD_URL_ALLOW_PRIVATE_NET:
        logger.warning(
            "upload.guardrails.permissive source=%s setting=UPLOAD_URL_ALLOW_PRIVATE_NET value=1",
            source,
        )


def _enforce_upload_size_limit(size_bytes: int) -> None:
    if UPLOAD_MAX_BYTES > 0 and size_bytes > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds configured max size ({UPLOAD_MAX_BYTES} bytes).",
        )


def _is_non_public_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _resolved_ips(hostname: str) -> list[ipaddress._BaseAddress]:
    host = (hostname or "").strip()
    if not host:
        return []
    try:
        literal = ipaddress.ip_address(host)
        return [literal]
    except ValueError:
        pass

    results: list[ipaddress._BaseAddress] = []
    seen: set[str] = set()
    infos = socket.getaddrinfo(host, None)
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            results.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    return results


def _validate_upload_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL.")

    scheme = parsed.scheme.strip().lower()
    if scheme not in UPLOAD_URL_ALLOWED_SCHEMES:
        allowed = ", ".join(UPLOAD_URL_ALLOWED_SCHEMES)
        raise ValueError(f"URL scheme '{scheme}' is not allowed. Allowed schemes: {allowed}.")

    if not UPLOAD_URL_ALLOW_PRIVATE_NET:
        hostname = parsed.hostname or ""
        if not hostname:
            raise ValueError("URL host is missing.")
        ips = _resolved_ips(hostname)
        if not ips:
            raise ValueError(f"Could not resolve host '{hostname}'.")
        if any(_is_non_public_ip(ip) for ip in ips):
            raise ValueError("URL host resolves to a non-public network address.")

    return parsed


class _LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects: int):
        super().__init__()
        self._max_redirects = max(0, int(max_redirects))

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_count = int(getattr(req, "_redirect_count", 0)) + 1
        if self._max_redirects > 0 and redirect_count > self._max_redirects:
            raise ValueError(f"Redirect limit exceeded ({self._max_redirects}).")
        _validate_upload_url(newurl)
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            setattr(new_req, "_redirect_count", redirect_count)
        return new_req


def _download_url_bytes(source_url: str) -> bytes:
    request = urllib.request.Request(source_url, headers={"User-Agent": "adk-ingestion/1.0"})
    opener = urllib.request.build_opener(_LimitedRedirectHandler(UPLOAD_URL_MAX_REDIRECTS))
    chunks: list[bytes] = []
    size_bytes = 0
    with opener.open(request, timeout=20) as resp:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            size_bytes += len(chunk)
            _enforce_upload_size_limit(size_bytes)
            chunks.append(chunk)
    return b"".join(chunks)


def _stl_bbox_filter(df: pd.DataFrame) -> pd.DataFrame:
    try:
        lat_col = next((col for col in GEO_LATITUDE_COLUMNS if col in df.columns), None)
        lon_col = next((col for col in GEO_LONGITUDE_COLUMNS if col in df.columns), None)
        if not lat_col or not lon_col:
            return df
        with postgis_store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH e AS (
                  SELECT ST_Extent(geom) AS ext
                  FROM roads
                  WHERE geom IS NOT NULL
                )
                SELECT ST_XMin(ext), ST_YMin(ext), ST_XMax(ext), ST_YMax(ext)
                FROM e
                """
            )
            row = cur.fetchone()
        if not row or any(value is None for value in row):
            return df
        minx, miny, maxx, maxy = row
        df = df.copy()
        df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
        df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
        return df[
            (df[lat_col] >= miny)
            & (df[lat_col] <= maxy)
            & (df[lon_col] >= minx)
            & (df[lon_col] <= maxx)
        ]
    except Exception:
        return df


def _analyze_codebook_needs(
    df: pd.DataFrame,
    ingest_stats: Optional[Dict[str, Any]],
    existing_mappings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profiles = (ingest_stats or {}).get("column_profiles") or {}
    try:
        return postgis_store.analyze_code_mappings_for_dataframe(
            df,
            column_profiles=profiles,
            existing_mappings=existing_mappings,
        )
    except Exception:
        return {
            "available": False,
            "attributes": 0,
            "matches": [],
            "candidates": [],
            "missing": [],
            "mappings": {},
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }


def _build_code_mapping_reminder(entity_type: str, codebook_info: Optional[Dict[str, Any]]) -> Optional[str]:
    entity = (entity_type or "").lower()
    if entity not in {"crash", "workzone"}:
        return None
    info = codebook_info or {}
    missing = info.get("missing") if isinstance(info, dict) else None
    if not isinstance(missing, list):
        return None
    candidates = [str(col).strip() for col in missing if str(col).strip()]
    if not candidates:
        return None
    sample = ", ".join(candidates[:4])
    suffix = ", ..." if len(candidates) > 4 else ""
    return (
        f"Coded columns detected ({sample}{suffix}). "
        "Upload attributes/codebook file to decode them."
    )


def _infer_upload_entity_type(label: str) -> str:
    lower = (label or "").lower()
    if any(token in lower for token in ("workzone", "work_zone", "wzdx", "construction")):
        return "workzone"
    if any(token in lower for token in ("crash", "accident")):
        return "crash"
    return "event"


def _seed_queryable_policy(entity_type: str) -> Optional[Dict[str, Any]]:
    entity = (entity_type or "").strip().lower()
    if entity == "crash":
        # Minimal crash defaults for new uploads. Keep this small and explicit.
        seed_fields = [
            {"query_name": "event_date", "source_column": "event_date", "enabled": True},
            {"query_name": "event_time", "source_column": "event_time", "enabled": True},
            {"query_name": "latitude", "source_column": "latitude", "enabled": True},
            {"query_name": "longitude", "source_column": "longitude", "enabled": True},
            {"query_name": "severity", "source_column": "severity", "enabled": True},
            {"query_name": "number_killed", "source_column": "number_killed", "enabled": True},
            {"query_name": "road_name", "source_column": "road_name", "enabled": True},
        ]
    elif entity == "workzone":
        # Keep workzone defaults concise but useful out of the box.
        seed_fields = [
            {"query_name": "start_date", "source_column": "start_date", "enabled": True},
            {"query_name": "end_date", "source_column": "end_date", "enabled": True},
            {"query_name": "road_name", "source_column": "road_name", "enabled": True},
            {"query_name": "latitude", "source_column": "latitude", "enabled": True},
            {"query_name": "longitude", "source_column": "longitude", "enabled": True},
            {"query_name": "vehicle_impact", "source_column": "vehicle_impact", "enabled": True},
            {"query_name": "location_method", "source_column": "location_method", "enabled": True},
            {"query_name": "reduced_speed_limit_kph", "source_column": "reduced_speed_limit_kph", "enabled": True},
        ]
    else:
        return None

    return {
        "entity_type": entity,
        "fields": resolve_queryable_fields(entity, {"fields": seed_fields}),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _flatten_geojson_points(coords: Any) -> List[Tuple[float, float]]:
    if not isinstance(coords, (list, tuple)):
        return []
    if (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        return [(float(coords[0]), float(coords[1]))]
    out: List[Tuple[float, float]] = []
    for item in coords:
        out.extend(_flatten_geojson_points(item))
    return out


def _geom_centroid_lon_lat(geom: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(geom, dict):
        return None, None
    gtype = str(geom.get("type", "")).strip()
    points: List[Tuple[float, float]] = []
    if gtype == "GeometryCollection":
        geoms = geom.get("geometries")
        if isinstance(geoms, list):
            for child in geoms:
                points.extend(_flatten_geojson_points((child or {}).get("coordinates")))
    else:
        points = _flatten_geojson_points(geom.get("coordinates"))
    if not points:
        return None, None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return float(sum(lons) / len(lons)), float(sum(lats) / len(lats))


def _parse_upload_content(filename: str, raw: bytes) -> pd.DataFrame:
    ext = Path(filename).suffix.lower()
    sample = raw.lstrip()[:1]
    looks_json = sample in (b"{", b"[")
    looks_xml = sample == b"<"

    if ext in (".json", ".geojson") or looks_json:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        feature_list: Optional[List[dict]] = None
        if isinstance(payload, dict):
            payload_type = str(payload.get("type", "")).strip().lower()
            if payload_type == "featurecollection" and isinstance(payload.get("features"), list):
                feature_list = payload.get("features") or []
            elif isinstance(payload.get("features"), list):
                feature_list = payload.get("features") or []
        if feature_list is not None:
            rows = []
            for feat in feature_list:
                if not isinstance(feat, dict):
                    continue
                props = feat.get("properties", {}) or {}
                if not isinstance(props, dict):
                    props = {"value": props}
                props = dict(props)
                geom = feat.get("geometry")
                lon, lat = _geom_centroid_lon_lat(geom)
                keys_lower = {str(key).lower() for key in props.keys()}
                if lat is not None and not ({"lat", "latitude"} & keys_lower):
                    props["latitude"] = lat
                if lon is not None and not ({"lon", "lng", "longitude"} & keys_lower):
                    props["longitude"] = lon
                props["geometry"] = geom
                rows.append(props)
            return pd.DataFrame(rows)
        return pd.json_normalize(payload)

    if ext in (".xml", ".gml") or looks_xml:
        try:
            df = pd.read_xml(io.BytesIO(raw), parser="etree")
        except Exception:
            try:
                df = pd.read_xml(io.BytesIO(raw), xpath=".//row", parser="etree")
            except Exception:
                df = pd.read_xml(io.BytesIO(raw), xpath=".//*", parser="etree")
        if df is None:
            raise ValueError("Unable to parse XML upload.")
        return df

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.BytesIO(raw))


async def upload_dataset(
    file: UploadFile,
    dataset_name: Optional[str] = None,
    entity_type: Optional[str] = None,
    x_session_id: Optional[str] = None,
) -> dict[str, Any]:
    logger = logging.getLogger("adk_server")
    t_start = time.perf_counter()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty upload.")
    _log_permissive_upload_guardrails(logger, "file")
    _enforce_upload_size_limit(len(content))

    filename = file.filename or "upload"
    name = (dataset_name or Path(filename).stem)[:80]
    logger.info(
        "upload.start file=%s size_bytes=%s dataset_name=%s session_id=%s",
        filename,
        len(content),
        name,
        x_session_id or get_active_session() or "",
    )

    resolved_entity_type = normalize_entity_type(entity_type) if entity_type else _infer_upload_entity_type(name)
    t_parse = time.perf_counter()
    df = await asyncio.to_thread(_parse_upload_content, filename, content)
    parse_ms = int((time.perf_counter() - t_parse) * 1000)
    logger.info(
        "upload.stage parsed file=%s rows=%s cols=%s parse_ms=%s entity_type=%s",
        filename,
        len(df),
        len(df.columns),
        parse_ms,
        resolved_entity_type,
    )

    t_codebook_detect = time.perf_counter()
    try:
        codebook_schema = await asyncio.to_thread(postgis_store.detect_codebook_schema, df)
    except Exception as exc:
        logger.warning("Codebook schema detection failed for %s: %s", filename, exc)
        codebook_schema = None
    codebook_detect_ms = int((time.perf_counter() - t_codebook_detect) * 1000)
    if codebook_schema:
        t_codebook_ingest = time.perf_counter()
        try:
            stats = await asyncio.to_thread(lambda: postgis_store.ingest_codebook_df(df, source=filename))
        except Exception as exc:
            logger.error("Codebook ingestion failed for %s: %s", filename, exc, exc_info=True)
            raise HTTPException(status_code=400, detail=f"Codebook detected but ingestion failed: {exc}")
        codebook_ingest_ms = int((time.perf_counter() - t_codebook_ingest) * 1000)

        refresh_warning = ""
        t_codebook_refresh = time.perf_counter()
        try:
            refresh = await asyncio.to_thread(
                lambda: postgis_store.refresh_session_codebook_info(sample_rows=800, max_datasets=3)
            )
        except Exception as exc:
            logger.warning("Codebook refresh failed after upload for %s: %s", filename, exc, exc_info=True)
            refresh = {"refreshed": 0, "failed": 0, "details": {}}
            refresh_warning = str(exc)
        codebook_refresh_ms = int((time.perf_counter() - t_codebook_refresh) * 1000)
        refreshed = refresh.get("refreshed", 0)
        total_ms = int((time.perf_counter() - t_start) * 1000)
        logger.info(
            "upload.complete_codebook file=%s rows=%s codebook_detect_ms=%s codebook_ingest_ms=%s codebook_refresh_ms=%s total_ms=%s refreshed=%s",
            filename,
            len(df),
            codebook_detect_ms,
            codebook_ingest_ms,
            codebook_refresh_ms,
            total_ms,
            refreshed,
        )
        return {
            "status": "success",
            "codebook": stats,
            "schema": codebook_schema,
            "refresh": refresh,
            "warning": refresh_warning,
            "timings": {
                "parse_ms": parse_ms,
                "codebook_detect_ms": codebook_detect_ms,
                "codebook_ingest_ms": codebook_ingest_ms,
                "codebook_refresh_ms": codebook_refresh_ms,
                "total_ms": total_ms,
            },
            "message": (
                "Codebook uploaded. "
                + (
                    f"Updated code mappings for {refreshed} dataset(s)."
                    if refreshed
                    else "Mappings will apply to future uploads."
                )
            ),
        }

    workzone_filter_ms = 0
    if resolved_entity_type == "workzone":
        t_workzone_filter = time.perf_counter()
        df = await asyncio.to_thread(_stl_bbox_filter, df)
        workzone_filter_ms = int((time.perf_counter() - t_workzone_filter) * 1000)

    t_create_ds = time.perf_counter()
    ds = await asyncio.to_thread(lambda: postgis_store.create_dataset(name=name, entity_type=resolved_entity_type))
    create_dataset_ms = int((time.perf_counter() - t_create_ds) * 1000)
    dataset_id = ds["dataset_id"]

    t_ingest = time.perf_counter()
    ingest = await asyncio.to_thread(postgis_store.ingest_events_df, dataset_id, df)
    ingest_ms = int((time.perf_counter() - t_ingest) * 1000)
    t_road_match = time.perf_counter()
    try:
        road = await asyncio.to_thread(
            lambda: postgis_store.map_events_to_osm_ways(dataset_id, max_dist_m=100.0, batch_size=10_000)
        )
    except Exception as exc:
        logger.warning("Road matching skipped (OSM roads may not be loaded): %s", exc)
        road = {"matched": 0, "skipped": True}
    road_match_ms = int((time.perf_counter() - t_road_match) * 1000)

    t_codebook_analyze = time.perf_counter()
    codebook_info = await asyncio.to_thread(_analyze_codebook_needs, df, ingest)
    codebook_analyze_ms = int((time.perf_counter() - t_codebook_analyze) * 1000)
    code_mapping_reminder = _build_code_mapping_reminder(resolved_entity_type, codebook_info)
    queryable_policy = _seed_queryable_policy(resolved_entity_type)
    t_mark_ready = time.perf_counter()
    ready_stats = {"ingest": ingest, "road_match": road, "codebook": codebook_info}
    if queryable_policy:
        ready_stats["queryable_fields"] = queryable_policy
    await asyncio.to_thread(
        lambda: postgis_store.mark_ready(
            dataset_id,
            stats=ready_stats,
            provenance_step={
                "op": "map_to_osm_ways",
                "max_dist_m": 100.0,
                "method": "nearest_within_osm_roads_match",
                "crs_m": 26915,
                "ts": datetime.utcnow().isoformat() + "Z",
            },
        )
    )
    mark_ready_ms = int((time.perf_counter() - t_mark_ready) * 1000)

    t_suggestions = time.perf_counter()
    all_columns = set(df.columns)
    column_categories = await asyncio.to_thread(categorize_columns, all_columns)
    query_suggestions = await asyncio.to_thread(
        generate_query_suggestions,
        all_columns,
        ingest.get("column_profiles"),
    )
    suggestions_ms = int((time.perf_counter() - t_suggestions) * 1000)

    data_summary_parts = []
    if column_categories.get("fatality"):
        data_summary_parts.append(f"Fatality columns: {', '.join(column_categories['fatality'][:3])}")
    if column_categories.get("injury"):
        data_summary_parts.append(f"Injury columns: {', '.join(column_categories['injury'][:3])}")
    if column_categories.get("severity"):
        data_summary_parts.append(f"Severity: {', '.join(column_categories['severity'][:3])}")
    if column_categories.get("location"):
        data_summary_parts.append(f"Location: {', '.join(column_categories['location'][:3])}")
    if column_categories.get("time"):
        data_summary_parts.append(f"Time: {', '.join(column_categories['time'][:3])}")

    data_summary = "; ".join(data_summary_parts) if data_summary_parts else "Various columns available"
    if codebook_info.get("available") and codebook_info.get("matches"):
        data_summary += f". Codebook matched columns: {', '.join(codebook_info['matches'][:6])}"
    elif codebook_info.get("available") and codebook_info.get("missing"):
        data_summary += (
            f". Columns needing code mapping: {', '.join(codebook_info['missing'][:6])}. "
            "Review in Ingestion Inspector."
        )
    elif codebook_info.get("missing"):
        data_summary += (
            f". Possible coded columns: {', '.join(codebook_info['missing'][:6])}. "
            "Upload an attributes/codebook file to decode."
        )

    total_ms = int((time.perf_counter() - t_start) * 1000)
    logger.info(
        "upload.complete file=%s dataset_id=%s rows=%s parse_ms=%s create_dataset_ms=%s ingest_ms=%s road_match_ms=%s codebook_detect_ms=%s codebook_analyze_ms=%s mark_ready_ms=%s suggestions_ms=%s workzone_filter_ms=%s total_ms=%s matched=%s/%s",
        filename,
        dataset_id,
        len(df),
        parse_ms,
        create_dataset_ms,
        ingest_ms,
        road_match_ms,
        codebook_detect_ms,
        codebook_analyze_ms,
        mark_ready_ms,
        suggestions_ms,
        workzone_filter_ms,
        total_ms,
        road.get("matched"),
        road.get("total"),
    )

    return {
        "status": "success",
        "dataset_id": dataset_id,
        "name": name,
        "entity_type": resolved_entity_type,
        "ingest": ingest,
        "road_match": road,
        "codebook": codebook_info,
        "code_mapping_reminder": code_mapping_reminder,
        "column_categories": column_categories,
        "query_suggestions": query_suggestions,
        "data_summary": data_summary,
        "timings": {
            "parse_ms": parse_ms,
            "create_dataset_ms": create_dataset_ms,
            "ingest_ms": ingest_ms,
            "road_match_ms": road_match_ms,
            "codebook_detect_ms": codebook_detect_ms,
            "codebook_analyze_ms": codebook_analyze_ms,
            "mark_ready_ms": mark_ready_ms,
            "suggestions_ms": suggestions_ms,
            "workzone_filter_ms": workzone_filter_ms,
            "total_ms": total_ms,
        },
    }


async def upload_dataset_url(
    url: str,
    dataset_name: Optional[str] = None,
    entity_type: Optional[str] = None,
    x_session_id: Optional[str] = None,
) -> dict[str, Any]:
    logger = logging.getLogger("adk_server")
    try:
        t_start = time.perf_counter()

        _log_permissive_upload_guardrails(logger, "url")
        parsed = _validate_upload_url(url)

        name = (dataset_name or Path(parsed.path).stem or "upload")[:80]
        resolved_entity_type = normalize_entity_type(entity_type) if entity_type else _infer_upload_entity_type(name)
        logger.info(
            "upload_url.start url=%s dataset_name=%s session_id=%s",
            url,
            name,
            x_session_id or get_active_session() or "",
        )

        t_download = time.perf_counter()
        content = await asyncio.to_thread(_download_url_bytes, url)
        download_ms = int((time.perf_counter() - t_download) * 1000)
        if not content:
            raise ValueError("URL returned empty content.")
        _enforce_upload_size_limit(len(content))

        filename = Path(parsed.path).name or "url_dataset.json"
        t_parse = time.perf_counter()
        df = await asyncio.to_thread(_parse_upload_content, filename, content)
        parse_ms = int((time.perf_counter() - t_parse) * 1000)
        logger.info(
            "upload_url.stage parsed file=%s size_bytes=%s rows=%s cols=%s parse_ms=%s download_ms=%s entity_type=%s",
            filename,
            len(content),
            len(df),
            len(df.columns),
            parse_ms,
            download_ms,
            resolved_entity_type,
        )

        t_codebook_detect = time.perf_counter()
        try:
            codebook_schema = await asyncio.to_thread(postgis_store.detect_codebook_schema, df)
        except Exception as exc:
            logger.warning("Codebook schema detection failed for URL upload %s: %s", filename, exc)
            codebook_schema = None
        codebook_detect_ms = int((time.perf_counter() - t_codebook_detect) * 1000)
        if codebook_schema:
            t_codebook_ingest = time.perf_counter()
            try:
                stats = await asyncio.to_thread(lambda: postgis_store.ingest_codebook_df(df, source=filename))
            except Exception as exc:
                logger.error("Codebook ingestion failed for URL upload %s: %s", filename, exc, exc_info=True)
                raise HTTPException(status_code=400, detail=f"Codebook detected but ingestion failed: {exc}")
            codebook_ingest_ms = int((time.perf_counter() - t_codebook_ingest) * 1000)

            refresh_warning = ""
            t_codebook_refresh = time.perf_counter()
            try:
                refresh = await asyncio.to_thread(
                    lambda: postgis_store.refresh_session_codebook_info(sample_rows=800, max_datasets=3)
                )
            except Exception as exc:
                logger.warning(
                    "Codebook refresh failed after URL upload %s: %s",
                    filename,
                    exc,
                    exc_info=True,
                )
                refresh = {"refreshed": 0, "failed": 0, "details": {}}
                refresh_warning = str(exc)
            codebook_refresh_ms = int((time.perf_counter() - t_codebook_refresh) * 1000)
            refreshed = refresh.get("refreshed", 0)
            total_ms = int((time.perf_counter() - t_start) * 1000)
            logger.info(
                "upload_url.complete_codebook file=%s rows=%s download_ms=%s parse_ms=%s codebook_detect_ms=%s codebook_ingest_ms=%s codebook_refresh_ms=%s total_ms=%s refreshed=%s",
                filename,
                len(df),
                download_ms,
                parse_ms,
                codebook_detect_ms,
                codebook_ingest_ms,
                codebook_refresh_ms,
                total_ms,
                refreshed,
            )
            return {
                "status": "success",
                "codebook": stats,
                "schema": codebook_schema,
                "refresh": refresh,
                "warning": refresh_warning,
                "timings": {
                    "download_ms": download_ms,
                    "parse_ms": parse_ms,
                    "codebook_detect_ms": codebook_detect_ms,
                    "codebook_ingest_ms": codebook_ingest_ms,
                    "codebook_refresh_ms": codebook_refresh_ms,
                    "total_ms": total_ms,
                },
                "message": (
                    "Codebook uploaded. "
                    + (
                        f"Updated code mappings for {refreshed} dataset(s)."
                        if refreshed
                        else "Mappings will apply to future uploads."
                    )
                ),
            }

        workzone_filter_ms = 0
        if resolved_entity_type == "workzone":
            t_workzone_filter = time.perf_counter()
            df = await asyncio.to_thread(_stl_bbox_filter, df)
            workzone_filter_ms = int((time.perf_counter() - t_workzone_filter) * 1000)

        t_create_ds = time.perf_counter()
        ds = await asyncio.to_thread(lambda: postgis_store.create_dataset(name=name, entity_type=resolved_entity_type))
        create_dataset_ms = int((time.perf_counter() - t_create_ds) * 1000)
        dataset_id = ds["dataset_id"]

        t_ingest = time.perf_counter()
        ingest = await asyncio.to_thread(postgis_store.ingest_events_df, dataset_id, df)
        ingest_ms = int((time.perf_counter() - t_ingest) * 1000)
        t_road_match = time.perf_counter()
        try:
            road = await asyncio.to_thread(
                lambda: postgis_store.map_events_to_osm_ways(dataset_id, max_dist_m=100.0, batch_size=10_000)
            )
        except Exception as exc:
            logger.warning("Road matching skipped for URL upload (OSM roads may not be loaded): %s", exc)
            road = {"matched": 0, "skipped": True}
        road_match_ms = int((time.perf_counter() - t_road_match) * 1000)

        t_codebook_analyze = time.perf_counter()
        codebook_info = await asyncio.to_thread(_analyze_codebook_needs, df, ingest)
        codebook_analyze_ms = int((time.perf_counter() - t_codebook_analyze) * 1000)
        code_mapping_reminder = _build_code_mapping_reminder(resolved_entity_type, codebook_info)
        queryable_policy = _seed_queryable_policy(resolved_entity_type)
        t_mark_ready = time.perf_counter()
        ready_stats = {"ingest": ingest, "road_match": road, "codebook": codebook_info}
        if queryable_policy:
            ready_stats["queryable_fields"] = queryable_policy
        await asyncio.to_thread(
            lambda: postgis_store.mark_ready(
                dataset_id,
                stats=ready_stats,
                provenance_step={
                    "op": "map_to_osm_ways",
                    "max_dist_m": 100.0,
                    "method": "nearest_within_osm_roads_match",
                    "crs_m": 26915,
                    "ts": datetime.utcnow().isoformat() + "Z",
                },
            )
        )
        mark_ready_ms = int((time.perf_counter() - t_mark_ready) * 1000)
        total_ms = int((time.perf_counter() - t_start) * 1000)
        logger.info(
            "upload_url.complete file=%s dataset_id=%s rows=%s download_ms=%s parse_ms=%s create_dataset_ms=%s ingest_ms=%s road_match_ms=%s codebook_detect_ms=%s codebook_analyze_ms=%s mark_ready_ms=%s workzone_filter_ms=%s total_ms=%s matched=%s/%s",
            filename,
            dataset_id,
            len(df),
            download_ms,
            parse_ms,
            create_dataset_ms,
            ingest_ms,
            road_match_ms,
            codebook_detect_ms,
            codebook_analyze_ms,
            mark_ready_ms,
            workzone_filter_ms,
            total_ms,
            road.get("matched"),
            road.get("total"),
        )

        return {
            "status": "success",
            "dataset_id": dataset_id,
            "name": name,
            "entity_type": resolved_entity_type,
            "ingest": ingest,
            "road_match": road,
            "codebook": codebook_info,
            "code_mapping_reminder": code_mapping_reminder,
            "timings": {
                "download_ms": download_ms,
                "parse_ms": parse_ms,
                "create_dataset_ms": create_dataset_ms,
                "ingest_ms": ingest_ms,
                "road_match_ms": road_match_ms,
                "codebook_detect_ms": codebook_detect_ms,
                "codebook_analyze_ms": codebook_analyze_ms,
                "mark_ready_ms": mark_ready_ms,
                "workzone_filter_ms": workzone_filter_ms,
                "total_ms": total_ms,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("URL upload failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))

"""Schema/profile inference helpers for ingestion."""

from __future__ import annotations

import re
from typing import Dict, Optional

import pandas as pd

from .geo_columns import GEO_LATITUDE_COLUMNS, GEO_LONGITUDE_COLUMNS

GEOMETRY_COLUMNS = ["geometry", "geom", "geojson", "wkt"]

ENTITY_HINTS = {
    "crash": ["crash", "accident", "collision", "incident", "severity", "cseverity", "crash_key"],
    "workzone": ["workzone", "work_zone", "wzdx", "construction"],
    "traffic": ["traffic", "speed", "volume", "flow", "travel_time"],
    "road": ["road", "segment", "travelway", "route", "highway"],
}

FIELD_HINTS = {
    "primary_id": ["crash_key", "crash_id", "incident_id", "event_id", "record", "uuid", "image", "image_no", "id"],
    "timestamp": ["timestamp", "datetime", "date_time", "event_datetime"],
    "event_date": ["event_date", "accident_date", "crash_date", "date", "crash_date"],
    "event_time": ["event_time", "accident_time", "crash_time", "time", "timestr"],
    "start_time": ["start_time", "start", "begin", "open_time", "start_date"],
    "end_time": ["end_time", "end", "close_time", "end_date"],
    "road_name": ["road_name", "road", "route", "street", "highway", "interstate", "travelway"],
    "road_id": ["road_segment_id", "segment_id", "road_id", "trf_info_seg_id", "travelway_id", "routeid"],
}

TIME_ONLY_PATTERN = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*([AP]M)?\s*$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
PLACEHOLDER_DATE_PATTERN = re.compile(r"(0001|1900)-01-01")


def _detect_geo_columns(df: pd.DataFrame) -> tuple[Optional[str], Optional[str], Optional[str]]:
    lat_col = next((col for col in GEO_LATITUDE_COLUMNS if col in df.columns), None)
    lon_col = next((col for col in GEO_LONGITUDE_COLUMNS if col in df.columns), None)
    geom_col = next((col for col in GEOMETRY_COLUMNS if col in df.columns), None)
    return lat_col, lon_col, geom_col


def _tokenize_column(name: str) -> list[str]:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    tokens = re.split(r"[^a-zA-Z0-9]+", name)
    return [token.lower() for token in tokens if token]


def _find_column(columns: list[str], keywords: list[str]) -> Optional[str]:
    lowered = [col.lower() for col in columns]
    tokenized = {col: _tokenize_column(col) for col in columns}

    for keyword in keywords:
        target = keyword.lower()
        if target in lowered:
            return columns[lowered.index(target)]

    for keyword in keywords:
        parts = [part for part in re.split(r"[^a-zA-Z0-9]+", keyword.lower()) if part]
        if not parts:
            continue
        for col in columns:
            tokens = tokenized[col]
            if all(part in tokens for part in parts):
                return col

    for keyword in keywords:
        target = keyword.lower()
        for col in columns:
            if target in tokenized[col]:
                return col

    for keyword in keywords:
        target = keyword.lower()
        if len(target) < 4:
            continue
        for idx, col in enumerate(lowered):
            if target in col:
                return columns[idx]

    return None


def _infer_entity_type(dataset_name: str, columns: list[str]) -> str:
    name = (dataset_name or "").lower()
    lowered = [col.lower() for col in columns]

    for entity, hints in ENTITY_HINTS.items():
        if any(hint in name for hint in hints):
            return entity
        if any(any(hint in col for hint in hints) for col in lowered):
            return entity
    return "other"


def _infer_mapping_fields(
    df: pd.DataFrame,
    metadata: dict,
    entity_type: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    columns = list(df.columns)
    timestamp_candidate = _find_column(columns, FIELD_HINTS["timestamp"])
    if timestamp_candidate and timestamp_candidate in df.columns:
        if _series_looks_time_only(df[timestamp_candidate]) or _series_has_placeholder_date(df[timestamp_candidate]):
            timestamp_candidate = None

    event_date_candidate = _find_column(columns, FIELD_HINTS["event_date"])
    event_time_candidate = _find_column(columns, FIELD_HINTS["event_time"])

    if event_time_candidate and event_time_candidate in df.columns:
        if not (_series_looks_time_only(df[event_time_candidate]) or _series_has_placeholder_date(df[event_time_candidate])):
            event_time_candidate = None

    start_time_candidate = _find_column(columns, FIELD_HINTS["start_time"])
    end_time_candidate = _find_column(columns, FIELD_HINTS["end_time"])

    entity = (entity_type or "").lower()
    if entity == "crash":
        start_time_candidate = None
        end_time_candidate = None
    elif entity == "workzone":
        event_date_candidate = None
        event_time_candidate = None

    fields: Dict[str, Optional[str]] = {
        "primary_id": _find_column(columns, FIELD_HINTS["primary_id"]),
        "timestamp": timestamp_candidate,
        "event_date": event_date_candidate,
        "event_time": event_time_candidate,
        "start_time": start_time_candidate,
        "end_time": end_time_candidate,
        "latitude": metadata.get("geo", {}).get("lat_column") or _find_column(columns, ["latitude", "lat"]),
        "longitude": metadata.get("geo", {}).get("lon_column") or _find_column(columns, ["longitude", "lon", "lng"]),
        "geometry": metadata.get("geo", {}).get("geometry_column") or _find_column(columns, GEOMETRY_COLUMNS),
        "road_name": metadata.get("road_match", {}).get("road_column") or _find_column(columns, FIELD_HINTS["road_name"]),
        "road_id": metadata.get("road_match", {}).get("road_segment_id_column") or _find_column(columns, FIELD_HINTS["road_id"]),
    }
    return fields


def _infer_capabilities(
    entity_type: Optional[str],
    mapping: Optional[dict],
    metadata: Optional[dict],
) -> list[str]:
    caps: set[str] = set()
    entity = (entity_type or "").lower()
    if entity in {"crash", "traffic", "workzone"}:
        caps.add(f"{entity}-compatible")

    meta = metadata or {}
    geo = meta.get("geo", {}) if isinstance(meta.get("geo", {}), dict) else {}
    if (geo.get("lat_column") and geo.get("lon_column")) or geo.get("geometry_column"):
        caps.add("geo-enabled")

    fields = {}
    if isinstance(mapping, dict):
        fields = mapping.get("fields") or {}
    if fields.get("road_name") or fields.get("road_id") or meta.get("road_match", {}).get("road_column"):
        caps.add("road-enabled")

    if any(fields.get(name) for name in ("timestamp", "event_date", "event_time", "start_time", "end_time")):
        caps.add("time-enabled")

    if "road-enabled" in caps and "time-enabled" in caps:
        caps.add("road-time-joinable")

    return sorted(caps)


def _explain_entity_guess(entity_type: str, columns: list[str]) -> str:
    hints = ENTITY_HINTS.get(entity_type, [])
    if not hints:
        return ""
    matches = []
    lowered = [col.lower() for col in columns]
    for hint in hints:
        for idx, col in enumerate(lowered):
            if hint in col:
                matches.append(columns[idx])
    if not matches:
        return ""
    unique = []
    for col in matches:
        if col not in unique:
            unique.append(col)
        if len(unique) >= 3:
            break
    return ", ".join(unique)


def _series_looks_time_only(series: pd.Series, sample_size: int = 50) -> bool:
    if series is None or not isinstance(series, pd.Series):
        return False
    sample = series.dropna().astype(str).head(sample_size)
    if sample.empty:
        return False
    matches = sample.apply(_value_looks_time_only)
    return matches.mean() >= 0.8


def _value_looks_time_only(value: str) -> bool:
    text = str(value).strip()
    if not text:
        return False
    if DATE_PATTERN.search(text):
        return False
    return bool(TIME_ONLY_PATTERN.match(text))


def _value_has_placeholder_date(value: str) -> bool:
    return bool(PLACEHOLDER_DATE_PATTERN.search(str(value)))


def _series_has_placeholder_date(series: pd.Series, sample_size: int = 50) -> bool:
    if series is None or not isinstance(series, pd.Series):
        return False
    sample = series.dropna().astype(str).head(sample_size)
    if sample.empty:
        return False
    matches = sample.apply(_value_has_placeholder_date)
    return matches.mean() >= 0.8


__all__ = [
    "_detect_geo_columns",
    "_infer_capabilities",
    "_infer_entity_type",
    "_infer_mapping_fields",
    "_explain_entity_guess",
]

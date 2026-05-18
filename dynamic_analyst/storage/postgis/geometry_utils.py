from __future__ import annotations

import json
import math
import warnings
from typing import Any, Dict, List, Optional

import pandas as pd

from ...geo_columns import GEO_LATITUDE_COLUMNS, GEO_LONGITUDE_COLUMNS


def _infer_lat_lon(df: pd.DataFrame) -> tuple[Optional[str], Optional[str]]:
    lat = next((c for c in GEO_LATITUDE_COLUMNS if c in df.columns), None)
    lon = next((c for c in GEO_LONGITUDE_COLUMNS if c in df.columns), None)
    return lat, lon


def _infer_ts_col(df: pd.DataFrame) -> Optional[str]:
    candidates = []
    for c in df.columns:
        lc = c.lower()
        if any(k in lc for k in ("timestamp", "datetime", "date_time", "event_time", "eventdate", "crash_time", "time")):
            candidates.append(c)
    for c in candidates:
        s = _parse_datetime_series(df[c])
        if s.notna().sum() > 0:
            return c
    return None


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return None
        try:
            out = float(txt)
            return out if math.isfinite(out) else None
        except Exception:
            return None
    return None


def _extract_geojson_geometry(value: Any) -> Optional[Dict[str, Any]]:
    obj: Any = value
    if obj is None:
        return None
    if isinstance(obj, str):
        txt = obj.strip()
        if not txt or txt.lower() in {"null", "none"}:
            return None
        try:
            obj = json.loads(txt)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    if str(obj.get("type", "")).strip().lower() == "feature":
        obj = obj.get("geometry")
        if not isinstance(obj, dict):
            return None

    gtype = str(obj.get("type", "")).strip()
    if not gtype:
        return None
    if gtype != "GeometryCollection" and obj.get("coordinates") is None:
        return None
    return obj


def _flatten_geojson_points(coords: Any) -> List[tuple[float, float]]:
    if not isinstance(coords, (list, tuple)):
        return []
    if (
        len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        return [(float(coords[0]), float(coords[1]))]
    out: List[tuple[float, float]] = []
    for item in coords:
        out.extend(_flatten_geojson_points(item))
    return out


def _geometry_centroid_lon_lat(geom: Optional[Dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(geom, dict):
        return None, None
    if str(geom.get("type", "")).strip() == "GeometryCollection":
        geoms = geom.get("geometries")
        if not isinstance(geoms, list):
            return None, None
        pts: List[tuple[float, float]] = []
        for child in geoms:
            pts.extend(_flatten_geojson_points((child or {}).get("coordinates")))
    else:
        pts = _flatten_geojson_points(geom.get("coordinates"))
    if not pts:
        return None, None
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return float(sum(lons) / len(lons)), float(sum(lats) / len(lats))


def _infer_geometry_col(df: pd.DataFrame) -> Optional[str]:
    preferred = [
        "geometry",
        "geom",
        "geojson",
        "wkb_geometry",
        "the_geom",
        "shape",
    ]
    lower_to_col = {str(c).lower(): c for c in df.columns}
    for cand in preferred:
        if cand in lower_to_col:
            return lower_to_col[cand]
    for c in df.columns:
        sample = df[c].dropna().head(25).tolist()
        for v in sample:
            if _extract_geojson_geometry(v):
                return c
    return None


def _parse_datetime_series(series: pd.Series, formats: Optional[list[str]] = None) -> pd.Series:
    if formats:
        for fmt in formats:
            s = pd.to_datetime(series, errors="coerce", utc=True, format=fmt)
            if s.notna().any():
                return s
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Could not infer format*", category=UserWarning)
        return pd.to_datetime(series, errors="coerce", utc=True)


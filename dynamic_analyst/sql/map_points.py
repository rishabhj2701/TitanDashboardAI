"""Map-point helper utilities shared by SQL/conflation modules."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..data_registry import _safe_value
from ..geo_columns import GEO_LATITUDE_COLUMNS, GEO_LONGITUDE_COLUMNS
from ..session_state import save_map_for_session


def _find_prefixed_column(df: pd.DataFrame, prefix: str, candidates: list[str]) -> Optional[str]:
    for name in candidates:
        candidate = f"{prefix}{name}"
        if candidate in df.columns:
            return candidate
    return None


def _build_map_points(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    frames: list[pd.DataFrame] = []
    left_lat = _find_prefixed_column(df, "left_", GEO_LATITUDE_COLUMNS)
    left_lon = _find_prefixed_column(df, "left_", GEO_LONGITUDE_COLUMNS)
    right_lat = _find_prefixed_column(df, "right_", GEO_LATITUDE_COLUMNS)
    right_lon = _find_prefixed_column(df, "right_", GEO_LONGITUDE_COLUMNS)

    if left_lat and left_lon:
        cols = [left_lat, left_lon]
        for col in (
            "road_name",
            "left_row_id",
            "left_primary_id",
            "left_event_date",
            "left_event_time",
            "left_time",
            "left_severity",
        ):
            if col in df.columns:
                cols.append(col)
        crash_points = df[cols].copy()
        if "left_row_id" in crash_points.columns:
            crash_points = crash_points.drop_duplicates(subset=["left_row_id"])
            crash_points = crash_points.drop(columns=["left_row_id"])
        crash_points = crash_points.rename(
            columns={
                left_lat: "latitude",
                left_lon: "longitude",
                "left_primary_id": "hp_acc_image_no",
                "left_event_date": "accident_date",
                "left_event_time": "accident_time",
                "left_time": "timestamp",
                "left_severity": "severity",
            }
        )
        crash_points["type"] = "Crash"
        crash_points["point_type"] = "Crash"
        frames.append(crash_points)

    if right_lat and right_lon:
        cols = [right_lat, right_lon]
        for col in (
            "road_name",
            "right_row_id",
            "right_speed",
            "right_SpeedLimitMPH",
            "right_time",
        ):
            if col in df.columns:
                cols.append(col)
        traffic_points = df[cols].copy()
        if "right_row_id" in traffic_points.columns:
            traffic_points = traffic_points.drop_duplicates(subset=["right_row_id"])
            traffic_points = traffic_points.drop(columns=["right_row_id"])
        traffic_points = traffic_points.rename(
            columns={
                right_lat: "latitude",
                right_lon: "longitude",
                "right_speed": "speed",
                "right_SpeedLimitMPH": "SpeedLimitMPH",
                "right_time": "timestamp",
            }
        )
        if "speed" in traffic_points.columns and "SpeedLimitMPH" in traffic_points.columns:
            traffic_points["speed_over_limit"] = (
                traffic_points["speed"] - traffic_points["SpeedLimitMPH"]
            )
        traffic_points["type"] = "Vehicle"
        traffic_points["point_type"] = "Traffic"
        frames.append(traffic_points)

    if not frames:
        return None

    return pd.concat(frames, ignore_index=True, sort=False)


def _map_payload_from_points_df(
    df: pd.DataFrame,
    label: str,
    map_type: str = "conflation",
    limit: int = 5000,
    label_column: Optional[str] = None,
) -> Optional[dict]:
    if df is None or df.empty:
        return None

    points = []
    for idx, row in df.head(limit).iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue

        point = {
            "id": str(idx),
            "latitude": lat_f,
            "longitude": lon_f,
        }

        if "type" in row and pd.notna(row["type"]):
            point["type"] = str(row["type"])
        if "point_type" in row and pd.notna(row["point_type"]):
            point["point_type"] = str(row["point_type"])

        for col, value in row.items():
            if col in {"latitude", "longitude", "type", "point_type"}:
                continue
            safe = _safe_value(value)
            if safe is None:
                continue
            point[col] = safe

        if label_column and label_column in row and pd.notna(row[label_column]):
            point["label"] = str(row[label_column])

        points.append(point)

    payload = {
        "label": f"{label} ({len(points)} points)",
        "count": len(points),
        "points": points,
    }
    save_map_for_session(payload, map_type=map_type)
    return payload


__all__ = [
    "_build_map_points",
    "_find_prefixed_column",
    "_map_payload_from_points_df",
]

"""Shared map/chart payload helpers."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import pandas as pd

from ..session_state import _json_safe_scalar, save_map_for_session


def _df_to_markdown_safe(df: pd.DataFrame, index: bool = False) -> str:
    try:
        return df.to_markdown(index=index)
    except Exception as exc:
        logging.getLogger("adk_server").warning(
            "to_markdown unavailable; falling back to to_string: %s", exc
        )
        return df.to_string(index=index)


def _make_map_payload(rows: list[dict], label: str = "Map Selection", hard_brake_only: bool = False) -> dict:
    points = []
    for r in rows:
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            continue

        point_type = str(r.get("type") or "Vehicle")
        point_category = str(
            r.get("point_type")
            or ("HardBrake" if point_type == "HardBrake" else "Traffic")
        )
        acc_x_val = r.get("acc_x")
        if hard_brake_only and acc_x_val is not None:
            try:
                if float(acc_x_val) <= -0.2:
                    point_type = "HardBrake"
                    point_category = "HardBrake"
            except (TypeError, ValueError):
                pass

        point = {
            "latitude": float(lat),
            "longitude": float(lon),
            "type": point_type,
            "point_type": point_category,
        }

        for k in (
            "timestamp",
            "speed",
            "speedLimit",
            "SpeedLimitMPH",
            "speed_over_limit",
            "acc_x",
            "acc_y",
            "roadName",
            "road_name",
            "roadSegmentId",
            "road_segment_id",
            "way_id",
        ):
            if k in r and r[k] is not None:
                point[k] = r[k]

        if "roadName" not in point and "road_name" in point:
            point["roadName"] = point["road_name"]
        if "speedLimit" not in point and "SpeedLimitMPH" in point:
            point["speedLimit"] = point["SpeedLimitMPH"]
        if "acc_x" in point and "acc_y" in point:
            try:
                point["acceleration"] = {
                    "x": float(point["acc_x"]),
                    "y": float(point["acc_y"]),
                }
            except (TypeError, ValueError):
                pass
        if point_type == "HardBrake" and "acc_x" in point:
            try:
                point["decelerationG"] = float(point["acc_x"])
            except (TypeError, ValueError):
                pass
        if "roadSegmentId" not in point and "road_segment_id" in point:
            point["roadSegmentId"] = point["road_segment_id"]
        if "roadSegmentId" not in point and "way_id" in point:
            point["roadSegmentId"] = str(point["way_id"])

        points.append(point)

    return {
        "label": f"{label} ({len(points)} points)",
        "count": len(points),
        "points": points,
    }


def _make_crash_map_payload(rows: list[dict], label: str = "Crash Map") -> dict:
    points = []
    for r in rows:
        lat = r.get("latitude")
        lon = r.get("longitude")
        if lat is None or lon is None:
            continue

        point = {
            "latitude": float(lat),
            "longitude": float(lon),
            "type": "Crash",
            "point_type": "Crash",
        }

        for k in (
            "timestamp",
            "severity",
            "primary_id",
            "hp_acc_image_no",
            "accident_date",
            "accident_time",
            "roadName",
            "road_name",
            "roadSegmentId",
            "road_segment_id",
        ):
            if k in r and r[k] is not None:
                point[k] = _json_safe_scalar(r[k])

        if "roadName" not in point and "road_name" in point:
            point["roadName"] = point["road_name"]
        if "roadSegmentId" not in point and "road_segment_id" in point:
            point["roadSegmentId"] = point["road_segment_id"]

        points.append(point)

    return {
        "label": f"{label} ({len(points)} points)",
        "count": len(points),
        "points": points,
    }


def _apply_codebook_labels(df: pd.DataFrame, dataset_id: Optional[str] = None) -> pd.DataFrame:
    try:
        from .. import postgis_store

        mapping = postgis_store.get_codebook_map_for_columns(list(df.columns), dataset_id=dataset_id)
    except Exception:
        mapping = {}

    if not mapping:
        return df

    for col, lookup in mapping.items():
        if col not in df.columns:
            continue
        label_col = f"{col}_label"

        def _map_value(val: Any) -> Optional[str]:
            if val is None:
                return None
            code = str(val).strip()
            if code in lookup:
                return lookup[code]
            return None

        df[label_col] = df[col].apply(_map_value)
    return df


_CHART_REQUEST_KEYWORDS = (
    "chart",
    "graph",
    "plot",
    "distribution",
    "histogram",
    "pie",
    "donut",
    "doughnut",
)


def _query_requests_chart(reasoning: str, steps: list[dict]) -> bool:
    step_ops = {
        str((step or {}).get("operation") or "").strip().lower()
        for step in (steps or [])
        if isinstance(step, dict)
    }
    # Guardrail: map-only plans should not auto-generate charts.
    # Example: "show crashes on map" often includes "visualize" in planner reasoning.
    if "generate_map" in step_ops and not (
        {"groupby", "aggregate", "unique_values", "head"} & step_ops
    ):
        return False
    blob = f"{reasoning or ''} {json.dumps(steps or [], default=str)}".lower()
    return any(keyword in blob for keyword in _CHART_REQUEST_KEYWORDS)


def _pick_groupby_value_column(df: pd.DataFrame, group_col: str) -> Optional[str]:
    preferred = ["count", f"{group_col}_count"]
    for candidate in preferred:
        if candidate in df.columns:
            numeric = pd.to_numeric(df[candidate], errors="coerce")
            if numeric.notna().any():
                return candidate

    for col in df.columns:
        if col == group_col:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.notna().any():
            return col
    return None


def _prefer_label_column(df: pd.DataFrame, base_col: Optional[str]) -> Optional[str]:
    if not base_col:
        return base_col
    label_col = f"{base_col}_label"
    if label_col not in df.columns:
        return base_col
    series = df[label_col]
    if not series.notna().any():
        return base_col
    text = series.astype(str).str.strip()
    if (text != "").any():
        return label_col
    return base_col


def _build_groupby_bar_chart_payload(
    df: pd.DataFrame,
    group_cols: list[str],
    dataset_id: str,
    reasoning: str,
    chart_role: str,
) -> Optional[dict]:
    if df is None or df.empty or not group_cols or len(group_cols) != 1:
        return None

    group_col = group_cols[0]
    if group_col not in df.columns:
        return None

    value_col = _pick_groupby_value_column(df, group_col)
    if not value_col:
        return None

    display_group_col = _prefer_label_column(df, group_col) or group_col
    chart_df = df[[display_group_col, value_col]].copy()
    chart_df[value_col] = pd.to_numeric(chart_df[value_col], errors="coerce")
    chart_df = chart_df.dropna(subset=[value_col])
    if chart_df.empty:
        return None

    chart_df = chart_df.head(50)
    labels = []
    for raw in chart_df[display_group_col].tolist():
        text = "" if raw is None else str(raw).strip()
        labels.append(text or "(blank)")
    values = [float(v) for v in chart_df[value_col].tolist()]

    if not labels or not values:
        return None

    x_base_col = display_group_col[:-6] if display_group_col.endswith("_label") else display_group_col
    x_label = x_base_col.replace("_", " ").title()
    value_label = "Count" if "count" in value_col.lower() else value_col.replace("_", " ").title()
    title = (
        f"Distribution of {x_label}"
        if ("distribution" in (reasoning or "").lower() or "count" in value_col.lower())
        else f"{value_label} by {x_label}"
    )

    return {
        "type": "bar",
        "title": title,
        "xLabel": x_label,
        "yLabel": value_label,
        "xValues": labels,
        "series": [{"label": value_label, "values": values}],
        "orientation": "vertical",
        "meta": {
            "chartRole": chart_role,
            "description": f"Grouped crash results from dataset {dataset_id}.",
            "columnsUsed": list(dict.fromkeys([group_col, display_group_col, value_col])),
            "table": [{x_base_col: labels[i], value_col: values[i]} for i in range(len(labels))],
        },
    }


def _requested_chart_type(reasoning: str, steps: list[dict]) -> str:
    blob = f"{reasoning or ''} {json.dumps(steps or [], default=str)}".lower()
    if "doughnut" in blob or "donut" in blob:
        return "doughnut"
    if "pie" in blob:
        return "pie"
    return "bar"


def _publish_chart_payload(chart_payload: list[dict], label: str = "Query chart") -> None:
    if not chart_payload:
        return
    save_map_for_session(
        {
            "label": label,
            "count": 0,
            "points": [],
            "analysis_type": "charts",
            "chartPayload": chart_payload,
        },
        map_type="charts",
    )


def _build_auto_chart_payload_from_df(
    df: pd.DataFrame,
    reasoning: str,
    steps: list[dict],
    chart_role: str,
    dataset_id: str,
    group_cols: Optional[list[str]] = None,
) -> Optional[dict]:
    if df is None or df.empty or not _query_requests_chart(reasoning, steps):
        return None

    preferred_type = _requested_chart_type(reasoning, steps)
    max_points = 50
    x_col: Optional[str] = None

    if group_cols and len(group_cols) == 1 and group_cols[0] in df.columns:
        x_col = group_cols[0]
    else:
        for col in df.columns:
            if col.lower().endswith("_id"):
                continue
            as_num = pd.to_numeric(df[col], errors="coerce")
            if as_num.notna().sum() == 0:
                uniq = df[col].dropna().astype(str).nunique()
                if 1 <= uniq <= max_points:
                    x_col = col
                    break

    numeric_candidates: list[str] = []
    for col in df.columns:
        if col == x_col:
            continue
        as_num = pd.to_numeric(df[col], errors="coerce")
        if as_num.notna().sum() > 0:
            numeric_candidates.append(col)

    if not numeric_candidates:
        return None

    selected_value_cols = numeric_candidates[:1] if preferred_type in {"pie", "doughnut"} else numeric_candidates[:3]
    chart_df = df.copy().head(max_points)
    if not x_col:
        x_col = "_row"
        chart_df = chart_df.reset_index(drop=True)
        chart_df[x_col] = chart_df.index + 1

    x_display_col = _prefer_label_column(chart_df, x_col) or x_col
    x_values: list[str] = []
    for raw in chart_df[x_display_col].tolist():
        text = "" if raw is None else str(raw).strip()
        if len(text) > 48:
            text = text[:45] + "..."
        x_values.append(text or "(blank)")

    if not x_values:
        return None

    series = []
    for col in selected_value_cols:
        vals = pd.to_numeric(chart_df[col], errors="coerce").tolist()
        has_value = any(pd.notna(v) for v in vals)
        if not has_value:
            continue
        normalized = [float(v) if pd.notna(v) else None for v in vals]
        series.append({"label": col.replace("_", " ").title(), "values": normalized})
    if not series:
        return None

    x_base_col = x_display_col[:-6] if x_display_col.endswith("_label") else x_display_col
    x_label = x_base_col.replace("_", " ").title() if x_base_col != "_row" else "Row"
    y_label = "Value" if len(series) > 1 else series[0]["label"]
    title = (
        f"Distribution of {x_label}"
        if ("distribution" in (reasoning or "").lower() and x_col != "_row")
        else f"{y_label} by {x_label}"
    )

    payload: dict[str, Any] = {
        "type": preferred_type,
        "title": title,
        "xLabel": x_label,
        "yLabel": y_label,
        "xValues": x_values,
        "series": series,
        "orientation": "vertical",
        "meta": {
            "chartRole": chart_role,
            "description": f"Auto-generated chart from SQL result ({dataset_id}).",
            "columnsUsed": list(dict.fromkeys([x_col, x_display_col] + selected_value_cols)),
            "table": [
                {
                    x_base_col: x_values[i],
                    **{selected_value_cols[j]: series[j]["values"][i] for j in range(len(series))},
                }
                for i in range(len(x_values))
            ],
        },
    }

    if preferred_type in {"pie", "doughnut"} and len(payload["series"]) > 1:
        payload["series"] = [payload["series"][0]]

    return payload


__all__ = [
    "_apply_codebook_labels",
    "_build_auto_chart_payload_from_df",
    "_build_groupby_bar_chart_payload",
    "_df_to_markdown_safe",
    "_make_crash_map_payload",
    "_make_map_payload",
    "_publish_chart_payload",
    "_query_requests_chart",
]

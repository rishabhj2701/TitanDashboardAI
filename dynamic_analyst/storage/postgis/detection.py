from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd

from ...data_registry import _safe_value


def profile_columns(df: pd.DataFrame, sample_size: int = 500) -> Dict[str, Any]:
    profiles = {}
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()

        dtype_str = str(series.dtype)
        if "int" in dtype_str or "float" in dtype_str:
            dtype_category = "numeric"
        elif "datetime" in dtype_str:
            dtype_category = "datetime"
        elif "bool" in dtype_str:
            dtype_category = "boolean"
        else:
            dtype_category = "text"

        if dtype_category == "text" and not non_null.empty:
            try:
                numeric_check = pd.to_numeric(non_null.head(50), errors="coerce")
                if numeric_check.notna().mean() > 0.8:
                    dtype_category = "numeric"
            except Exception:
                pass

        try:
            unique_count = int(non_null.nunique()) if not non_null.empty else 0
        except TypeError:
            unique_count = -1
        unique_values = None
        if dtype_category == "text" and 0 < unique_count <= 20:
            try:
                unique_values = sorted([str(v) for v in non_null.unique() if v is not None][:20])
            except Exception:
                unique_values = None

        sample_values = []
        if not non_null.empty:
            sample_values = [_safe_value(v) for v in non_null.head(3).tolist()]

        suggested_uses = []
        col_lower = col.lower()

        if dtype_category == "numeric":
            suggested_uses.extend(["filter", "aggregate"])
            if any(k in col_lower for k in ("count", "num", "number", "total", "killed", "injured")):
                suggested_uses.append("sum")
        elif dtype_category == "datetime":
            suggested_uses.extend(["filter", "time_range"])
        elif dtype_category == "text":
            if unique_count <= 20:
                suggested_uses.extend(["filter", "group_by"])
            else:
                suggested_uses.append("filter")

        if any(k in col_lower for k in ("severity", "type", "category", "class", "status")):
            if "group_by" not in suggested_uses:
                suggested_uses.append("group_by")
        if any(k in col_lower for k in ("lat", "lon", "latitude", "longitude")):
            suggested_uses = ["mapping"]
        if any(k in col_lower for k in ("road", "street", "route", "highway")):
            suggested_uses.extend(["filter", "group_by"])

        profiles[col] = {
            "dtype": dtype_str,
            "dtype_category": dtype_category,
            "null_count": int(series.isna().sum()),
            "non_null_count": int(len(non_null)),
            "unique_count": unique_count,
            "unique_values": unique_values,
            "sample_values": sample_values,
            "suggested_uses": list(set(suggested_uses)),
        }

    return {
        "column_profiles": profiles,
        "total_columns": len(profiles),
        "profiled_at": datetime.utcnow().isoformat() + "Z",
    }


def detect_codebook_schema(df: pd.DataFrame) -> Optional[Dict[str, str]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    exact = _detect_codebook_schema_exact(df)
    if exact:
        return exact

    if len(df.columns) >= 25:
        return None
    if _looks_like_event_dataset(df):
        return None

    return _detect_codebook_schema_heuristic(df)


def _detect_codebook_schema_exact(df: pd.DataFrame) -> Optional[Dict[str, str]]:
    col_lower = {c.lower(): c for c in df.columns}

    def _pick(candidates: list[str]) -> Optional[str]:
        for c in candidates:
            if c.lower() in col_lower:
                return col_lower[c.lower()]
        return None

    attr_col = _pick(["hp_cd_val_attr", "attr", "attribute", "field", "column", "code_attr"])
    code_col = _pick(["hp_cd_val_code", "code", "value", "code_value"])
    label_col = _pick(["description", "desc", "label", "meaning", "name", "corr_tms_name", "corr_cve_name"])
    entity_col = _pick(["hp_cd_val_entity", "entity", "table", "dataset"])

    if attr_col and code_col and label_col:
        return {
            "attr": attr_col,
            "code": code_col,
            "label": label_col,
            "entity": entity_col or "",
        }
    return None


_CODEBOOK_ROLE_HINTS = {
    "attr": {"attr", "attribute", "field", "column", "category", "dimension", "domain", "element"},
    "code": {"code", "value", "val", "id", "key", "enum"},
    "label": {"label", "name", "desc", "description", "meaning", "title", "text"},
    "entity": {"entity", "table", "dataset", "subject", "module", "source"},
}


def _codebook_col_tokens(name: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or ""))
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", text.lower()) if tok]


def _codebook_series_profile(series: pd.Series, sample_size: int = 500) -> Dict[str, Any]:
    non_null = series.dropna().astype(str).map(lambda v: v.strip())
    non_null = non_null[non_null != ""]
    if non_null.empty:
        return {
            "n": 0,
            "unique": 0,
            "unique_ratio": 0.0,
            "avg_len": 0.0,
            "max_len": 0,
            "pct_numeric": 0.0,
            "pct_alnum": 0.0,
            "pct_spaces": 0.0,
            "pct_upper_snake": 0.0,
            "pct_alpha": 0.0,
        }

    s = non_null.head(sample_size)
    n = int(len(s))
    unique = int(s.nunique())
    lens = s.map(len)
    numeric_mask = s.str.match(r"^-?\d+(\.\d+)?$")
    alnum_mask = s.str.match(r"^[A-Za-z0-9]+$")
    spaces_mask = s.str.contains(r"\s", regex=True)
    upper_snake_mask = s.str.match(r"^[A-Z0-9_]+$")
    alpha_mask = s.str.contains(r"[A-Za-z]", regex=True)

    return {
        "n": n,
        "unique": unique,
        "unique_ratio": float(unique / n) if n else 0.0,
        "avg_len": float(lens.mean()) if n else 0.0,
        "max_len": int(lens.max()) if n else 0,
        "pct_numeric": float(numeric_mask.mean()) if n else 0.0,
        "pct_alnum": float(alnum_mask.mean()) if n else 0.0,
        "pct_spaces": float(spaces_mask.mean()) if n else 0.0,
        "pct_upper_snake": float(upper_snake_mask.mean()) if n else 0.0,
        "pct_alpha": float(alpha_mask.mean()) if n else 0.0,
        "pct_date_like": float(s.str.match(r"^\\d{4}-\\d{2}-\\d{2}").mean()) if n else 0.0,
    }


def _looks_like_event_dataset(df: pd.DataFrame) -> bool:
    cols = [str(c).strip().lower() for c in df.columns if str(c).strip()]
    if not cols:
        return False

    lat_tokens = ("lat", "latitude")
    lon_tokens = ("lon", "lng", "longitude")
    time_tokens = ("timestamp", "datetime", "event_time", "event_date", "date", "time", "ts")
    domain_tokens = (
        "crash",
        "accident",
        "severity",
        "road",
        "speed",
        "injury",
        "killed",
        "vehicle",
        "workzone",
        "wzdx",
        "sensor",
        "location",
    )
    all_tokens = lat_tokens + lon_tokens + time_tokens + domain_tokens

    has_lat = any(any(tok in col for tok in lat_tokens) for col in cols)
    has_lon = any(any(tok in col for tok in lon_tokens) for col in cols)
    has_geo_pair = bool(has_lat and has_lon)

    time_hits = sum(1 for col in cols if any(tok in col for tok in time_tokens))
    domain_hits = sum(1 for col in cols if any(tok in col for tok in domain_tokens))
    token_hits = sum(1 for col in cols if any(tok in col for tok in all_tokens))

    if has_geo_pair and time_hits >= 1:
        return True
    if has_geo_pair and domain_hits >= 2:
        return True
    if domain_hits >= 4 and time_hits >= 1:
        return True

    return token_hits >= 6


def _codebook_role_score(role: str, col_name: str, profile: Dict[str, Any]) -> float:
    tokens = set(_codebook_col_tokens(col_name))
    hints = _CODEBOOK_ROLE_HINTS.get(role, set())
    name_score = 2.0 * len(tokens & hints)

    avg_len = float(profile.get("avg_len", 0.0))
    unique = int(profile.get("unique", 0))
    unique_ratio = float(profile.get("unique_ratio", 0.0))
    pct_numeric = float(profile.get("pct_numeric", 0.0))
    pct_spaces = float(profile.get("pct_spaces", 0.0))
    pct_upper_snake = float(profile.get("pct_upper_snake", 0.0))
    pct_alpha = float(profile.get("pct_alpha", 0.0))

    value_score = 0.0
    if role == "attr":
        if pct_upper_snake >= 0.5:
            value_score += 1.0
        if 3 <= avg_len <= 40:
            value_score += 0.5
        if unique >= 2 and 0.02 <= unique_ratio <= 1.0:
            value_score += 0.5
        if pct_spaces <= 0.4:
            value_score += 0.3
        if pct_numeric <= 0.5:
            value_score += 0.2
    elif role == "code":
        if avg_len <= 8:
            value_score += 1.0
        if pct_numeric >= 0.5:
            value_score += 0.8
        if unique >= 2:
            value_score += 0.2
        if unique_ratio < 0.9:
            value_score += 0.4
        if pct_spaces <= 0.2:
            value_score += 0.3
    elif role == "label":
        if avg_len >= 4:
            value_score += 0.6
        if pct_spaces >= 0.2:
            value_score += 1.1
        if pct_alpha >= 0.6:
            value_score += 0.5
        if unique >= 2:
            value_score += 0.2
    elif role == "entity":
        if pct_upper_snake >= 0.5:
            value_score += 0.6
        if unique <= 20:
            value_score += 0.8
        if unique_ratio <= 0.5:
            value_score += 0.7
        if 3 <= avg_len <= 40:
            value_score += 0.2

    return name_score + value_score


def _codebook_combo_score(df: pd.DataFrame, attr_col: str, code_col: str, label_col: str) -> float:
    base = 0.0
    try:
        sample = df[[attr_col, code_col, label_col]].dropna().head(4000)
    except Exception:
        return base
    if sample.empty:
        return base
    try:
        by_attr_codes = sample.groupby(attr_col)[code_col].nunique()
        if not by_attr_codes.empty and float(by_attr_codes.median()) >= 2.0:
            base += 0.8
    except Exception:
        pass
    try:
        by_code_labels = sample.groupby(code_col)[label_col].nunique()
        if not by_code_labels.empty and float(by_code_labels.median()) <= 2.0:
            base += 0.8
    except Exception:
        pass
    try:
        code_avg = sample[code_col].astype(str).str.len().mean()
        label_avg = sample[label_col].astype(str).str.len().mean()
        if label_avg > code_avg + 1:
            base += 0.3
    except Exception:
        pass
    return base


def _detect_codebook_schema_heuristic(df: pd.DataFrame) -> Optional[Dict[str, str]]:
    cols = [c for c in df.columns if str(c).strip()]
    if len(cols) < 3:
        return None

    profiles = {c: _codebook_series_profile(df[c]) for c in cols}
    attr_scores = {c: _codebook_role_score("attr", c, profiles[c]) for c in cols}
    code_scores = {c: _codebook_role_score("code", c, profiles[c]) for c in cols}
    label_scores = {c: _codebook_role_score("label", c, profiles[c]) for c in cols}
    entity_scores = {c: _codebook_role_score("entity", c, profiles[c]) for c in cols}

    top_attr = [c for c, _ in sorted(attr_scores.items(), key=lambda x: x[1], reverse=True)[:8]]
    top_code = [c for c, _ in sorted(code_scores.items(), key=lambda x: x[1], reverse=True)[:8]]
    top_label = [c for c, _ in sorted(label_scores.items(), key=lambda x: x[1], reverse=True)[:8]]

    best: Optional[tuple[str, str, str, float]] = None
    for attr_col in top_attr:
        for code_col in top_code:
            if code_col == attr_col:
                continue
            for label_col in top_label:
                if label_col in {attr_col, code_col}:
                    continue
                score = (
                    attr_scores[attr_col]
                    + code_scores[code_col]
                    + label_scores[label_col]
                    + _codebook_combo_score(df, attr_col, code_col, label_col)
                )
                if best is None or score > best[3]:
                    best = (attr_col, code_col, label_col, score)

    if best is None:
        return None

    attr_col, code_col, label_col, best_score = best
    if (
        best_score < 4.0
        or attr_scores.get(attr_col, 0.0) < 1.0
        or code_scores.get(code_col, 0.0) < 1.0
        or label_scores.get(label_col, 0.0) < 1.0
    ):
        return None

    if not _passes_codebook_quality_checks(df, attr_col, code_col, label_col):
        return None

    entity_col = ""
    remaining = [c for c in cols if c not in {attr_col, code_col, label_col}]
    if remaining:
        candidate = max(remaining, key=lambda c: entity_scores.get(c, 0.0))
        if entity_scores.get(candidate, 0.0) >= 1.2:
            entity_col = candidate

    return {
        "attr": attr_col,
        "code": code_col,
        "label": label_col,
        "entity": entity_col,
    }


def _passes_codebook_quality_checks(df: pd.DataFrame, attr_col: str, code_col: str, label_col: str) -> bool:
    try:
        sample = df[[attr_col, code_col, label_col]].dropna().head(6000)
    except Exception:
        return False
    if sample.empty or len(sample) < 8:
        return False

    attr_s = sample[attr_col].astype(str).str.strip()
    code_s = sample[code_col].astype(str).str.strip()
    label_s = sample[label_col].astype(str).str.strip()
    keep = (attr_s != "") & (code_s != "") & (label_s != "")
    sample = sample[keep]
    if sample.empty or len(sample) < 8:
        return False

    attr_s = sample[attr_col].astype(str).str.strip()
    code_s = sample[code_col].astype(str).str.strip()
    label_s = sample[label_col].astype(str).str.strip()

    n = len(sample)
    attr_unique = int(attr_s.nunique())
    code_unique = int(code_s.nunique())
    label_unique = int(label_s.nunique())

    if attr_unique < 2 or code_unique < 2 or label_unique < 2:
        return False

    try:
        pair_label_nu = sample.groupby([attr_col, code_col])[label_col].nunique()
        if pair_label_nu.empty:
            return False
        pair_consistency = float((pair_label_nu <= 1).mean())
    except Exception:
        return False
    if pair_consistency < 0.85:
        return False

    attr_profile = _codebook_series_profile(attr_s)
    code_profile = _codebook_series_profile(code_s)
    label_profile = _codebook_series_profile(label_s)

    if attr_profile.get("pct_date_like", 0.0) > 0.1:
        return False
    if attr_profile.get("pct_alpha", 0.0) < 0.5:
        return False
    if attr_profile.get("avg_len", 0.0) < 3.0:
        return False

    if code_profile.get("avg_len", 0.0) > 14.0:
        return False
    if code_profile.get("pct_spaces", 0.0) > 0.2:
        return False

    if label_profile.get("pct_alpha", 0.0) < 0.5:
        return False
    if label_profile.get("avg_len", 0.0) < 3.0:
        return False

    if attr_unique / max(1, n) > 0.7:
        return False

    return True


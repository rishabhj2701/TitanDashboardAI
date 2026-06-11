"""Normalize wild natural-language queries into SQL plans with map output."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _text_lower(text: str) -> str:
    return str(text or "").strip().lower()


def sanitize_query_text(text: str) -> str:
    """Fix common typos like 'showUS 6 E' -> 'show US 6 E'."""
    raw = str(text or "").strip()
    if not raw:
        return raw
    raw = re.sub(r"(?i)\bshow(?=(us|i|mo)\b)", "show ", raw)
    raw = re.sub(r"(?i)\bshow(?=(all|me)\b)", "show ", raw)
    raw = re.sub(r"\bma\b", "am", raw, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", raw).strip()


def query_explicitly_table_only(user_query: str) -> bool:
    text = _text_lower(user_query)
    if not text:
        return False
    tokens = (
        "just the number",
        "only the number",
        "count only",
        "how many only",
        "table only",
        "spreadsheet",
        "csv",
        "export rows",
        "list columns",
        "what columns",
    )
    if any(token in text for token in tokens):
        return True
    if "how many" in text and not any(
        token in text
        for token in ("show", "display", "map", "visualize", "plot", "see", "view")
    ):
        return True
    return False


def query_requests_map(user_query: str) -> bool:
    text = _text_lower(user_query)
    if not text or query_explicitly_table_only(text):
        return False
    map_tokens = (
        "show me",
        "show all",
        "display",
        "on the map",
        "on map",
        "map it",
        "map them",
        "visualize",
        "visualise",
        "plot on",
        "see on",
        "view on",
        "where are",
        "locate",
        "all crashes",
        "all the crashes",
        "crashes on",
        "crash on",
        "accidents on",
        "accident on",
        "on i ",
        "on us ",
        "on route",
        "along i",
        "along us",
        "hard breaking",
        "hard braking",
        "braking point",
        "braking points",
        "decel",
        "highlight",
        "map it",
        "yes map",
        "cant see",
        "can't see",
        "cannot see",
        "not showing",
        "show the map",
    )
    if text.startswith("show ") or text.startswith("display "):
        return True
    return any(token in text for token in map_tokens)


def is_map_rerun_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return False
    return any(
        token in text
        for token in (
            "cant see",
            "can't see",
            "cannot see",
            "not showing",
            "not visible",
            "map it",
            "yes map",
            "show on map",
            "show the map",
            "see it on",
            "put it on the map",
        )
    )


def _clock_to_minutes(hour: int, minute: int, ampm: Optional[str]) -> int:
    h = int(hour)
    m = int(minute or 0)
    ap = str(ampm or "am").strip().lower()
    if h == 12:
        h = 0
    if ap.startswith("p"):
        h += 12
    return max(0, min(24 * 60 - 1, h * 60 + m))


def _minutes_to_time_str(total_minutes: int) -> str:
    mins = max(0, min(24 * 60 - 1, int(total_minutes)))
    return f"{mins // 60:02d}:{mins % 60:02d}:00"


def _minutes_to_time_str_end(total_minutes: int) -> str:
    """Inclusive end-of-minute for SQL time BETWEEN (e.g. 08:05:59)."""
    mins = max(0, min(24 * 60 - 1, int(total_minutes)))
    return f"{mins // 60:02d}:{mins % 60:02d}:59"


def extract_time_of_day_window(user_query: str) -> Optional[Tuple[str, str]]:
    """Parse phrases like 'between 8-8:05 am' -> ('08:00:00', '08:05:59')."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return None
    patterns = [
        r"(?:between|from)\s+(\d{1,2}):(\d{2})\s*(am|pm)\s+(?:and|to)\s+(\d{1,2}):(\d{2})\s*(am|pm)",
        r"(?:between|from)\s+(\d{1,2})\s*(?::(\d{2}))?\s*(?:-|to|and)\s*(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)?",
        r"\b(\d{1,2})\s*-\s*(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\b",
        r"(\d{1,2})\s*(?::(\d{2}))?\s*-\s*(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)",
        r"(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\s*-\s*(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 4:
            h1, h2, m2, ampm = groups
            start_m = _clock_to_minutes(int(h1), 0, ampm)
            end_m = _clock_to_minutes(int(h2), int(m2 or 0), ampm)
        elif len(groups) == 5:
            h1, m1, h2, m2, ampm = groups
            start_m = _clock_to_minutes(int(h1), int(m1 or 0), ampm)
            end_m = _clock_to_minutes(int(h2), int(m2 or 0), ampm)
        elif len(groups) == 6:
            h1, m1, ap1, h2, m2, ap2 = groups
            start_m = _clock_to_minutes(int(h1), int(m1 or 0), ap1)
            end_m = _clock_to_minutes(int(h2), int(m2 or 0), ap2)
        else:
            continue
        if end_m < start_m:
            end_m = min(start_m + 5, 24 * 60 - 1)
        return _minutes_to_time_str(start_m), _minutes_to_time_str_end(end_m)
    return None


def extract_top_n(user_query: str, default: int = 10) -> int:
    text = _text_lower(sanitize_query_text(user_query))
    match = None
    for pattern in (
        r"\btop\s+(\d{1,3})\b",
        r"\b(\d{1,3})\s+segment",
        r"\bshow\s+(\d{1,3})\s+segment",
    ):
        match = re.search(pattern, text)
        if match:
            break
    if not match:
        return max(1, min(50, int(default)))
    try:
        return max(1, min(50, int(match.group(1))))
    except (TypeError, ValueError):
        return max(1, min(50, int(default)))


def query_mentions_avg_speed(user_query: str) -> bool:
    """True when the user means observed/CV average speed, not posted speed limit."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return False
    if "speed limit" in text or ("speed" in text and "limit" in text):
        return False
    if any(
        phrase in text
        for phrase in ("average speed", "avg speed", "mean speed", "observed speed")
    ):
        return True
    return bool(re.search(r"\b(average|avg|mean)\b", text) and "speed" in text)


def extract_speed_limit_range(
    user_query: str,
) -> Optional[Tuple[float, float]]:
    """Parse ranges like '5-25 mph', '5 to 25 mph', 'between 5 and 25 mph'."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return None
    patterns = (
        r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\s*mph\b",
        r"\b(\d{1,3})\s+to\s+(\d{1,3})\s*mph\b",
        r"\bbetween\s+(\d{1,3})\s*mph\s+and\s+(\d{1,3})\s*mph\b",
        r"\bbetween\s+(\d{1,3})\s+and\s+(\d{1,3})\s*mph\b",
        r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            lo = float(match.group(1))
            hi = float(match.group(2))
            if lo > hi:
                lo, hi = hi, lo
            return lo, hi
        except (TypeError, ValueError):
            continue
    return None


def extract_speed_limit_mph(user_query: str, default: float = 25.0) -> float:
    text = _text_lower(sanitize_query_text(user_query))
    match = re.search(r"\b(\d{2,3})\s*mph\b", text)
    if match:
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            pass
    return float(default)


def extract_accident_date(user_query: str) -> Optional[str]:
    """Parse YYYY-MM-DD or M/D/YYYY from natural language."""
    text = sanitize_query_text(user_query)
    if not text:
        return None
    iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        return f"{iso.group(1)}-{int(iso.group(2)):02d}-{int(iso.group(3)):02d}"
    us = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if us:
        return f"{us.group(3)}-{int(us.group(1)):02d}-{int(us.group(2)):02d}"
    month_names = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
    for idx, name in enumerate(month_names, start=1):
        pat = rf"\b{name}\s+(\d{{1,2}})(?:,?\s+(20\d{{2}}))?\b"
        match = re.search(pat, text, flags=re.IGNORECASE)
        if match:
            year = match.group(2) or "2021"
            return f"{year}-{idx:02d}-{int(match.group(1)):02d}"
    return None


def extract_specific_clock_time(user_query: str) -> Optional[Tuple[str, str]]:
    """Single clock time -> narrow window (minute or 15 min), not a full hour."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return None
    patterns = [
        r"\bat\s+(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\b",
        r"\b(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)\b",
        r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 3 and groups[2] in ("am", "pm"):
            start_m = _clock_to_minutes(int(groups[0]), int(groups[1] or 0), groups[2])
            has_minutes = groups[1] is not None and str(groups[1]).isdigit()
        elif len(groups) >= 3 and groups[1] is not None and str(groups[1]).isdigit():
            hour = int(groups[0])
            minute = int(groups[1])
            ampm = groups[2] if len(groups) > 2 and groups[2] in ("am", "pm") else "am"
            if hour > 23:
                continue
            start_m = _clock_to_minutes(hour, minute, ampm)
            has_minutes = True
        else:
            continue
        if has_minutes:
            end_m = start_m
            return _minutes_to_time_str(start_m), _minutes_to_time_str_end(end_m)
        hour_start = (start_m // 60) * 60
        end_m = min(hour_start + 14, 24 * 60 - 1)
        return _minutes_to_time_str(hour_start), _minutes_to_time_str_end(end_m)
    return None


def query_uses_placeholder_datetime(user_query: str) -> bool:
    """User asked for 'specific day/date and time (range)' without giving values."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text:
        return False
    has_date_hint = bool(re.search(r"specific\s+(?:day|date)", text))
    has_time_hint = bool(
        re.search(r"specific\s+time(?:\s+range)?", text)
        or re.search(r"specific\s+date\s+and\s+time(?:\s+range)?", text)
    )
    return has_date_hint and has_time_hint


PLACEHOLDER_DATETIME_NOTE = (
    "Using demo defaults (no date/time in your message): 2024-01-09, 9:45–9:59 AM Iowa time. "
    "Say e.g. 'plot crashes on I-80 on 2024-06-15 between 8:00 and 8:15 am' to use your own."
)


def build_placeholder_datetime_conditions() -> List[Dict[str, Any]]:
    return [
        {"column": "accident_date", "operator": "=", "value": "2024-01-09"},
        {"column": "local_time", "operator": "between", "value": ["09:45:00", "09:59:59"]},
    ]


def build_crash_datetime_filter_conditions(user_query: str) -> List[Dict[str, Any]]:
    conditions: List[Dict[str, Any]] = []
    accident_date = extract_accident_date(user_query)
    if accident_date:
        conditions.append(
            {"column": "accident_date", "operator": "=", "value": accident_date}
        )
    window = extract_time_of_day_window(user_query) or extract_specific_clock_time(
        user_query
    )
    if window:
        start_t, end_t = window
        conditions.append(
            {"column": "local_time", "operator": "between", "value": [start_t, end_t]}
        )
    elif query_uses_placeholder_datetime(user_query) and not accident_date:
        conditions.extend(build_placeholder_datetime_conditions())
    return conditions


def is_top_crash_segments_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or not is_crash_query(text) or query_explicitly_table_only(text):
        return False
    has_corridor = bool(extract_route_refs(text)) or bool(
        re.search(r"\bi\s*[- ]?\s*80\b", text)
    )
    has_rank = any(
        token in text
        for token in (
            "top ",
            "highest",
            "most crash",
            "most accidents",
            "number of crash",
            "highest number",
            "crash count",
            "based on number",
            "ranked by",
            "total number of crash",
        )
    ) or bool(re.search(r"\btop\s+\d{1,3}\b", text))
    has_segment = any(
        token in text for token in ("segment", "segments", "road segment")
    )
    return has_corridor and has_rank and (has_segment or "crash" in text)


def is_crash_speed_limit_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or not is_crash_query(text):
        return False
    has_limit = (
        "speed limit" in text
        or "speed_limit" in text
        or ("speed" in text and "limit" in text)
        or bool(re.search(r"speeds?\s+(lower|less|below|under)\s+than", text))
    )
    has_low = any(
        token in text
        for token in (
            "lower than",
            "less than",
            "below",
            "under",
            "<",
            "slow",
        )
    ) or bool(re.search(r"<\s*\d{2}", text)) or bool(
        re.search(r"speeds?\s+(lower|less|below|under)\s+than", text)
    )
    has_segment = "segment" in text
    return has_limit and has_low and (has_segment or "iowa" in text or "all crash" in text)


def is_avg_speed_range_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or is_crash_query(text):
        return False
    if not extract_speed_limit_range(text) or not query_mentions_avg_speed(text):
        return False
    has_road = any(token in text for token in ("road", "roads", "segment", "highway"))
    return has_road and "speed" in text


def is_crash_avg_speed_range_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or not is_crash_query(text):
        return False
    if not extract_speed_limit_range(text) or not query_mentions_avg_speed(text):
        return False
    return any(token in text for token in ("road", "roads", "segment", "highway", "crash"))


def is_speed_limit_range_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or is_crash_query(text):
        return False
    if not extract_speed_limit_range(text) or query_mentions_avg_speed(text):
        return False
    has_road = any(token in text for token in ("road", "roads", "segment", "highway"))
    has_speed = "speed" in text
    return has_road and has_speed


def is_speed_limit_roads_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or is_crash_query(text) or is_speed_limit_range_intent(text):
        return False
    if is_avg_speed_near_intent(text) or is_avg_speed_range_intent(text):
        return False
    has_road = any(token in text for token in ("road", "roads", "segment", "highway"))
    has_limit = "speed limit" in text or ("speed" in text and "limit" in text)
    has_target = bool(re.search(r"\b\d{2,3}\s*mph\b", text)) or "25" in text
    has_avg = any(token in text for token in ("average", "avg", "mean"))
    return has_road and has_limit and has_target and (has_avg or "show" in text or "roads" in text)


def is_avg_speed_near_intent(user_query: str) -> bool:
    """Road segments with observed average speed near a single mph value (e.g. ~25 mph)."""
    text = _text_lower(sanitize_query_text(user_query))
    if not text or is_crash_query(text) or extract_speed_limit_range(text):
        return False
    if not query_mentions_avg_speed(text):
        return False
    has_road = any(token in text for token in ("road", "roads", "segment", "highway"))
    has_mph = bool(re.search(r"\b\d{2,3}\s*mph\b", text))
    return has_road and has_mph and "speed" in text


def build_avg_speed_near_steps(user_query: str) -> List[dict]:
    mph = extract_speed_limit_mph(user_query, default=25.0)
    tolerance = 3.0
    if re.search(r"\b(around|about|near|approximately)\b", _text_lower(user_query)):
        tolerance = 3.0
    return [
        {
            "operation": "roads_by_speed_limit",
            "params": {
                "speed_limit_mph": mph,
                "tolerance": tolerance,
                "metric": "avg_speed",
                "limit": 500,
                "generate_map": True,
            },
        }
    ]


def is_crash_corridor_datetime_intent(user_query: str) -> bool:
    text = sanitize_query_text(user_query)
    if not text or not is_crash_query(text):
        return False
    has_corridor = bool(extract_route_refs(text)) or bool(
        re.search(r"\bi\s*[- ]?\s*80\b", text, flags=re.IGNORECASE)
    )
    has_when = bool(
        extract_accident_date(text)
        or extract_time_of_day_window(text)
        or extract_specific_clock_time(text)
        or query_uses_placeholder_datetime(text)
        or re.search(
            r"\b(specific day|specific date|specific time|on\s+\d|at\s+\d|between\s+\d)",
            _text_lower(text),
        )
    )
    wants_map = query_requests_map(text) or "plot" in _text_lower(text)
    return has_corridor and has_when and wants_map


def build_top_crash_segments_steps(user_query: str) -> List[dict]:
    n = extract_top_n(user_query, default=10)
    corridor_conditions = build_corridor_filter_conditions(user_query)
    steps: List[dict] = []
    if corridor_conditions:
        steps.append(
            {
                "operation": "filter",
                "params": {"mode": "or", "conditions": corridor_conditions},
            }
        )
    steps.extend(
        [
            {
                "operation": "groupby",
                "params": {
                    "group_by": ["road_segment_id", "road_name"],
                    "aggregations": {
                        "crash_count": {
                            "fn": "count",
                            "column": "count",
                            "alias": "crash_count",
                        }
                    },
                },
            },
            {"operation": "sort", "params": {"sort_by": "crash_count", "order": "desc"}},
            {"operation": "head", "params": {"n": n}},
            {"operation": "generate_map", "params": {"limit": 5000, "per_cell": 5}},
        ]
    )
    return steps


def build_crash_speed_limit_steps(user_query: str) -> List[dict]:
    mph = extract_speed_limit_mph(user_query, default=25.0)
    text = _text_lower(sanitize_query_text(user_query))
    speed_col = "speed_limit_mph"
    if query_mentions_avg_speed(user_query):
        speed_col = "avg_speed_mph"
    conditions: List[Dict[str, Any]] = [
        {
            "column": speed_col,
            "operator": "<",
            "value": mph,
        }
    ]
    return [
        {
            "operation": "filter",
            "params": {"mode": "and", "conditions": conditions},
        },
        {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}},
    ]


def build_speed_limit_roads_steps(user_query: str) -> List[dict]:
    mph = extract_speed_limit_mph(user_query, default=25.0)
    return [
        {
            "operation": "roads_by_speed_limit",
            "params": {
                "speed_limit_mph": mph,
                "tolerance": 0.5,
                "limit": 500,
                "generate_map": True,
            },
        }
    ]


def build_speed_limit_range_steps(user_query: str) -> List[dict]:
    lo, hi = extract_speed_limit_range(user_query) or (5.0, 25.0)
    return [
        {
            "operation": "roads_by_speed_limit",
            "params": {
                "speed_limit_min_mph": lo,
                "speed_limit_max_mph": hi,
                "metric": "speed_limit",
                "limit": 500,
                "generate_map": True,
            },
        }
    ]


def build_avg_speed_range_steps(user_query: str) -> List[dict]:
    lo, hi = extract_speed_limit_range(user_query) or (5.0, 25.0)
    return [
        {
            "operation": "roads_by_speed_limit",
            "params": {
                "speed_limit_min_mph": lo,
                "speed_limit_max_mph": hi,
                "metric": "avg_speed",
                "limit": 500,
                "generate_map": True,
            },
        }
    ]


def build_crash_avg_speed_range_steps(user_query: str) -> List[dict]:
    lo, hi = extract_speed_limit_range(user_query) or (5.0, 25.0)
    return [
        {
            "operation": "filter",
            "params": {
                "mode": "and",
                "conditions": [
                    {
                        "column": "avg_speed_mph",
                        "operator": "between",
                        "value": [lo, hi],
                    }
                ],
            },
        },
        {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}},
    ]


def is_crash_count_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or not is_crash_query(text):
        return False
    return "how many" in text or bool(re.search(r"\bcount\b", text) and "crash" in text)


def build_crash_count_steps(user_query: str, *, with_map: bool = False) -> List[dict]:
    """Count crashes with optional time-of-day filter; map is optional display only."""
    conditions = build_crash_datetime_filter_conditions(user_query)
    steps: List[dict] = []
    if conditions:
        steps.append(
            {
                "operation": "filter",
                "params": {"mode": "and", "conditions": conditions},
            }
        )
    steps.append(
        {
            "operation": "aggregate",
            "params": {
                "aggregations": {
                    "crash_count": {
                        "fn": "count",
                        "column": "count",
                        "alias": "crash_count",
                    }
                }
            },
        }
    )
    if with_map:
        steps.append(
            {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}}
        )
    return steps


def extract_crash_count_threshold(user_query: str, default: int = 500) -> int:
    text = _text_lower(sanitize_query_text(user_query))
    match = re.search(
        r"(?:less|under|below|fewer)\s+than\s+(\d{1,5})\b", text
    ) or re.search(r"\bunder\s+(\d{1,5})\b", text)
    if match:
        try:
            return max(1, int(match.group(1)))
        except (TypeError, ValueError):
            pass
    return int(default)


def find_calendar_15min_crash_window(
    *,
    max_count: int = 500,
    dataset_id: Optional[str] = None,
    min_count: int = 15,
    prefer_lowest: bool = False,
) -> Optional[Tuple[str, str, str, int]]:
    """
    Pick a calendar date + 15-minute local clock window.
    prefer_lowest: fewest crashes; else highest count still under max_count.
    Returns (accident_date, start_time, end_time, count).
    """
    from dynamic_analyst.sql.catalog import _latest_event_dataset_id
    from dynamic_analyst.sql.constants import CRASH_TIMEZONE
    from dynamic_analyst.storage.postgis.db_pool import get_db_connection
    from dynamic_analyst.storage.postgis.table_names import APP_EVENTS

    resolved_id = (str(dataset_id).strip() if dataset_id else None) or _latest_event_dataset_id(
        "crash"
    )
    if not resolved_id:
        return None

    sql_prefix = f"""
        WITH buckets AS (
          SELECT
            (e.ts AT TIME ZONE '{CRASH_TIMEZONE}')::date AS d,
            date_trunc('hour', e.ts AT TIME ZONE '{CRASH_TIMEZONE}') +
              (floor(extract(minute from e.ts AT TIME ZONE '{CRASH_TIMEZONE}') / 15)
                * interval '15 min') AS bucket_start,
            count(*)::int AS n
          FROM {APP_EVENTS} e
          WHERE e.dataset_id = %s AND e.ts IS NOT NULL
          GROUP BY 1, 2
        )
        SELECT
          d::text,
          to_char(bucket_start::time, 'HH24:MI:SS'),
          to_char((bucket_start + interval '14 minutes 59 seconds')::time, 'HH24:MI:SS'),
          n
        FROM buckets
    """

    def _fetch(*, under_max: bool, min_n: int) -> Optional[Tuple[str, str, str, int]]:
        where_clause = "WHERE n >= %s" if prefer_lowest else "WHERE n < %s AND n >= %s"
        order_dir = "ASC" if prefer_lowest else "DESC"
        query_sql = (
            sql_prefix + f" {where_clause} ORDER BY n {order_dir} LIMIT 1"
        )
        params: tuple = (
            (resolved_id, int(min_n))
            if prefer_lowest
            else (resolved_id, int(max_count), int(min_n))
        )
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(query_sql, params)
                row = cur.fetchone()
        except Exception:
            return None
        if not row:
            return None
        return str(row[0]), str(row[1]), str(row[2]), int(row[3])

    if prefer_lowest:
        return _fetch(under_max=False, min_n=1)
    return _fetch(under_max=True, min_n=int(min_count)) or _fetch(under_max=True, min_n=1)


def is_sparse_crash_interval_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or not is_crash_query(text):
        return False
    if not re.search(r"\b15[\s-]*(?:min|minute)s?\b", text):
        return False
    wants_lowest = any(
        token in text
        for token in (
            "lowest",
            "fewest",
            "minimum",
            "least number",
            "smallest number",
        )
    )
    wants_limit = bool(
        re.search(r"(?:less|under|below|fewer)\s+than\s+\d+", text)
    ) or "500" in text
    wants_pick = any(
        phrase in text
        for phrase in (
            "tell me",
            "find",
            "give me",
            "a time",
            "time frame",
            "timeframe",
            "which",
            "what time",
            "suggest",
            "example",
            "time window",
            "visualize",
        )
    )
    return (wants_lowest or wants_limit) and (
        wants_pick or query_requests_map(text) or "visualize" in text
    )


def build_sparse_crash_interval_steps(user_query: str) -> List[dict]:
    text = _text_lower(sanitize_query_text(user_query))
    prefer_lowest = any(
        token in text
        for token in ("lowest", "fewest", "minimum", "least number", "smallest number")
    )
    max_count = extract_crash_count_threshold(user_query, default=500)
    picked = find_calendar_15min_crash_window(
        max_count=max_count, prefer_lowest=prefer_lowest
    )
    if not picked:
        return []
    accident_date, start_t, end_t, crash_n = picked
    if prefer_lowest:
        note = (
            f"Lowest 15-minute crash window in the dataset: "
            f"{accident_date} {start_t[:5]}–{end_t[:5]} Iowa local time — {crash_n:,} crash"
            f"{'es' if crash_n != 1 else ''}. "
            f"(Daily repeating 15-minute clock slots each have 1,000+ crashes; "
            f"this is one specific calendar date + time.)"
        )
    else:
        note = (
            f"Sparse 15-minute window (under {max_count:,} crashes): "
            f"{accident_date} {start_t[:5]}–{end_t[:5]} Iowa local time — {crash_n:,} crashes. "
            f"(A repeating daily 15-minute clock slot always has 1,000+ crashes in this dataset; "
            f"this window is one specific calendar date + time.)"
        )
    return [
        {
            "operation": "filter",
            "params": {
                "mode": "and",
                "conditions": [
                    {"column": "accident_date", "operator": "=", "value": accident_date},
                    {"column": "local_time", "operator": "between", "value": [start_t, end_t]},
                ],
                "sparse_window_note": note,
            },
        },
        {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}},
    ]


def build_crash_corridor_datetime_steps(user_query: str) -> List[dict]:
    corridor_conditions = build_corridor_filter_conditions(user_query)
    datetime_conditions = build_crash_datetime_filter_conditions(user_query)
    used_placeholder = query_uses_placeholder_datetime(user_query) and not (
        extract_accident_date(user_query)
        or extract_time_of_day_window(user_query)
        or extract_specific_clock_time(user_query)
    )
    steps: List[dict] = []
    nested_groups: List[Dict[str, Any]] = []
    if corridor_conditions:
        nested_groups.append({"mode": "or", "conditions": corridor_conditions})
    if datetime_conditions:
        nested_groups.append({"mode": "and", "conditions": datetime_conditions})
    if nested_groups:
        filter_params: Dict[str, Any] = {"mode": "and", "conditions": nested_groups}
        if used_placeholder:
            filter_params["datetime_placeholder_note"] = PLACEHOLDER_DATETIME_NOTE
        steps.append(
            {
                "operation": "filter",
                "params": filter_params,
            }
        )
    steps.append(
        {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}}
    )
    return steps


def build_crash_time_window_filter_conditions(user_query: str) -> List[Dict[str, Any]]:
    window = extract_time_of_day_window(user_query)
    if not window:
        return []
    start_t, end_t = window
    return [
        {"column": "local_time", "operator": "between", "value": [start_t, end_t]},
    ]


def build_crash_time_window_steps(user_query: str) -> List[dict]:
    conditions = build_crash_time_window_filter_conditions(user_query)
    if not conditions:
        return []
    return [
        {"operation": "filter", "params": {"mode": "and", "conditions": conditions}},
        {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}},
    ]


def is_crash_time_window_intent(user_query: str) -> bool:
    text = sanitize_query_text(user_query)
    if not text or not is_crash_query(text):
        return False
    return extract_time_of_day_window(text) is not None


def is_crash_query(user_query: str) -> bool:
    text = _text_lower(user_query)
    return any(token in text for token in ("crash", "crashes", "accident", "accidents", "collision"))


def is_traffic_query(user_query: str) -> bool:
    text = _text_lower(user_query)
    return any(
        token in text
        for token in (
            "traffic",
            "cv ",
            "connected vehicle",
            "speed",
            "hard brake",
            "hard braking",
            "hardbrake",
        )
    )


def extract_route_refs(text: str, max_items: int = 6) -> List[str]:
    raw = str(text or "")
    found: List[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"\b(i|us|mo|sr|route)\s*[- ]?\s*(\d{1,3})\b",
        raw,
        flags=re.IGNORECASE,
    ):
        prefix = match.group(1).upper()
        if prefix == "ROUTE":
            prefix = "MO"
        number = match.group(2)
        ref = f"{prefix}-{number}"
        if ref in seen:
            continue
        seen.add(ref)
        found.append(ref)
        if len(found) >= max(1, int(max_items)):
            break
    # Compact forms: i80, I80
    for match in re.finditer(r"\b(i|us)(\d{1,3})\b", raw, flags=re.IGNORECASE):
        ref = f"{match.group(1).upper()}-{match.group(2)}"
        if ref not in seen:
            seen.add(ref)
            found.append(ref)
    return found


def corridor_ilike_patterns(user_query: str, route_refs: Optional[List[str]] = None) -> List[str]:
    """Build ILIKE patterns for Iowa-style road labels (spaces, not only hyphens)."""
    patterns: set[str] = set()
    refs = list(route_refs or []) or extract_route_refs(user_query)
    for ref in refs:
        m = re.match(r"^([A-Z]+)-(\d+)$", ref.strip().upper())
        if not m:
            continue
        prefix, number = m.group(1), m.group(2)
        patterns.add(f"%{prefix} {number}%")
        patterns.add(f"%{prefix}-{number}%")
        patterns.add(f"%{prefix}{number}%")
    text = str(user_query or "")
    for match in re.finditer(r"\b(i|us)\s*[- ]?\s*(\d{1,3})\b", text, flags=re.IGNORECASE):
        prefix = match.group(1).upper()
        number = match.group(2)
        patterns.add(f"%{prefix} {number}%")
        patterns.add(f"%{prefix}-{number}%")
        patterns.add(f"%{prefix}{number}%")
    return sorted(patterns)


def extract_named_road_label(user_query: str) -> Optional[str]:
    """Extract a corridor label such as 'US 6 E' from show/map phrasing."""
    text = sanitize_query_text(user_query)
    if not text:
        return None
    patterns = [
        r"(?:show|display|map|highlight|view|see)\s+(?:me\s+)?((?:us|i|mo)\s*\d+\s*[ewns]?)\b",
        r"(?:show|display|map)\s+(?:me\s+)?((?:state\s+of\s+iowa,?\s*)?(?:us|i|mo)\s*\d+\s*[ewns]?)\b",
        r"\b((?:us|i|mo)\s*\d+\s*[ewns]?)\s*:?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1).strip()).upper()
        label = re.sub(r"^STATE OF IOWA,?\s*", "", label, flags=re.IGNORECASE).strip()
        if label:
            return label
    return None


def extract_county_road_label(user_query: str) -> Optional[str]:
    """Extract labels like 'County of Cass, Fairview Road, E' from show/map phrasing."""
    text = sanitize_query_text(user_query)
    if not text:
        return None

    show_match = re.search(
        r"(?i)(?:show|display|map|highlight|view|see)\s+(?:me\s+)?(?:the\s+)?(.+)$",
        text,
    )
    if show_match:
        candidate = re.sub(r"\s+", " ", show_match.group(1).strip()).strip(" .,;")
        if candidate and (
            re.search(r"(?i)\bcounty\s+of\b", candidate)
            or candidate.count(",") >= 1
        ):
            return candidate.upper()

    bare = text.strip().strip(" .,;")
    if bare and re.search(r"(?i)\bcounty\s+of\b", bare):
        return bare.upper()
    if bare and re.search(r",\s*[ewns]\s*$", bare, flags=re.IGNORECASE) and bare.count(",") >= 1:
        return bare.upper()
    return None


def extract_show_road_label(user_query: str) -> Optional[str]:
    """Highway (US/I) or county-style road label for map highlight intents."""
    return extract_named_road_label(user_query) or extract_county_road_label(user_query)


def is_show_named_road_intent(user_query: str) -> bool:
    text = sanitize_query_text(user_query)
    if not text or is_crash_query(text) or query_explicitly_table_only(text):
        return False
    label = extract_show_road_label(text)
    if not label:
        return False
    return query_requests_map(text) or bool(re.search(r"(?i)\b(show|display|map|highlight)\b", text))


def is_show_all_hard_braking_intent(user_query: str) -> bool:
    text = _text_lower(sanitize_query_text(user_query))
    if not text or query_explicitly_table_only(text):
        return False
    has_hb = any(
        token in text
        for token in (
            "hard brak",
            "hardbrak",
            "hard braking",
            "hard breaking",
            "decel",
            "braking point",
        )
    )
    has_show = query_requests_map(text) or text.startswith("show ")
    has_scope_all = any(token in text for token in ("all", "every", "any"))
    has_points = "point" in text
    if not has_hb or not has_show:
        return False
    if is_crash_query(text):
        return False
    return has_scope_all or has_points or "on the map" in text


def build_named_road_filter_conditions(label: str) -> List[Dict[str, Any]]:
    clean = re.sub(r"\s+", " ", str(label or "").strip()).upper()
    if not clean:
        return []
    patterns = {f"%{clean}%", f"%{clean.replace(' ', '%')}%"}
    if "," in clean:
        for part in [p.strip() for p in clean.split(",") if p.strip()]:
            if len(part) >= 4:
                patterns.add(f"%{part}%")
    m = re.match(r"^([A-Z]+)\s*(\d+)\s*([EWNS])?$", clean)
    if m:
        prefix, number, suffix = m.group(1), m.group(2), m.group(3) or ""
        patterns.add(f"%{prefix} {number}{(' ' + suffix) if suffix else ''}%")
        patterns.add(f"%{prefix}-{number}{suffix}%")
        patterns.add(f"%STATE OF IOWA, {prefix} {number}{(' ' + suffix) if suffix else ''}%")
    conditions: List[Dict[str, Any]] = []
    for pattern in sorted(patterns):
        for column in ("road_name", "label", "road", "ref", "name"):
            conditions.append(
                {"column": column, "operator": "ilike", "value": pattern}
            )
    return conditions


def build_named_road_steps(user_query: str) -> List[dict]:
    label = extract_show_road_label(user_query)
    if not label:
        return [{"operation": "generate_map", "params": {}}]
    conditions = build_named_road_filter_conditions(label)
    return [
        {
            "operation": "filter",
            "params": {"mode": "or", "conditions": conditions},
        },
        {"operation": "generate_map", "params": {"limit": 40}},
    ]


def build_show_all_hard_braking_steps(limit: int = 50) -> List[dict]:
    cap = max(5, min(80, int(limit)))
    return [
        {
            "operation": "filter",
            "params": {
                "mode": "and",
                "conditions": [
                    {"column": "hard_brake_count", "operator": ">", "value": 0}
                ],
            },
        },
        {
            "operation": "groupby",
            "params": {
                "group_by": ["label"],
                "aggregations": {
                    "hard_brake_count": {
                        "fn": "sum",
                        "column": "hard_brake_count",
                        "alias": "hard_brake_count",
                    }
                },
            },
        },
        {"operation": "sort", "params": {"sort_by": "hard_brake_count", "order": "desc"}},
        {"operation": "head", "params": {"n": cap}},
        {"operation": "generate_map", "params": {"limit": cap}},
    ]


def build_corridor_filter_conditions(
    user_query: str,
    route_refs: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    patterns = corridor_ilike_patterns(user_query, route_refs)
    if not patterns:
        return []
    conditions: List[Dict[str, Any]] = []
    refs = list(route_refs or []) or extract_route_refs(user_query)
    for ref in refs:
        m = re.match(r"^([A-Z]+)-(\d+)$", ref.strip().upper())
        if m:
            conditions.append(
                {
                    "column": "road_name",
                    "operator": "contains",
                    "value": f"{m.group(1)} {m.group(2)}",
                }
            )
    if not conditions:
        for pattern in patterns[:1]:
            conditions.append(
                {"column": "road_name", "operator": "ilike", "value": pattern}
            )
    return conditions


def _has_operation(steps: List[dict], operation: str) -> bool:
    target = str(operation or "").strip().lower()
    return any(
        isinstance(step, dict)
        and str(step.get("operation") or "").strip().lower() == target
        for step in steps
    )


def _steps_have_road_filter(steps: List[dict]) -> bool:
    road_cols = {"road", "road_name", "name", "road_ref", "ref", "routeid", "route_id"}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "").strip().lower() != "filter":
            continue
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        for cond in params.get("conditions") or []:
            if not isinstance(cond, dict):
                continue
            col = str(cond.get("column") or "").strip().lower()
            if col in road_cols:
                return True
    return False


def _merge_filter_conditions(
    steps: List[dict],
    new_conditions: List[Dict[str, Any]],
    *,
    mode: str = "or",
) -> List[dict]:
    if not new_conditions:
        return list(steps)

    out: List[dict] = []
    merged = False
    for step in steps:
        if not isinstance(step, dict):
            out.append(step)
            continue
        if str(step.get("operation") or "").strip().lower() != "filter" or merged:
            out.append(step)
            continue
        params = dict(step.get("params") or {})
        existing = params.get("conditions") if isinstance(params.get("conditions"), list) else []
        params["conditions"] = list(existing) + list(new_conditions)
        params["mode"] = str(params.get("mode") or mode).strip().lower() or mode
        out.append({"operation": "filter", "params": params})
        merged = True

    if not merged:
        out.insert(
            0,
            {
                "operation": "filter",
                "params": {"mode": mode, "conditions": list(new_conditions)},
            },
        )
    return out


def normalize_execute_query_plan(
    domain: str,
    steps: List[dict],
    reasoning: str,
    *,
    dataset_id: Optional[str] = None,
) -> Tuple[str, List[dict], Optional[str]]:
    """
    Adjust domain/steps so geographic questions produce map output and corridor filters.
    Returns (domain, steps, dataset_id).
    """
    text = sanitize_query_text(reasoning or "")
    lowered = _text_lower(text)
    steps_out: List[dict] = [step for step in (steps or []) if isinstance(step, dict)]
    domain_out = str(domain or "").strip().lower()
    dataset_out = (str(dataset_id).strip() if dataset_id else None) or None

    refs = extract_route_refs(text)
    corridor_conditions = build_corridor_filter_conditions(text, refs)
    wants_map = query_requests_map(text)

    time_conditions = build_crash_time_window_filter_conditions(text)

    if is_top_crash_segments_intent(text):
        steps_ranked = build_top_crash_segments_steps(text)
        if not _has_operation(steps_ranked, "generate_map"):
            steps_ranked.append(
                {"operation": "generate_map", "params": {"limit": 10000, "per_cell": 6}}
            )
        return "crash", steps_ranked, dataset_out

    if is_sparse_crash_interval_intent(text):
        steps_sparse = build_sparse_crash_interval_steps(text)
        if steps_sparse:
            return "crash", steps_sparse, dataset_out

    if is_crash_count_intent(text):
        return "crash", build_crash_count_steps(text, with_map=query_requests_map(text)), dataset_out

    if is_crash_speed_limit_intent(text):
        return "crash", build_crash_speed_limit_steps(text), dataset_out

    if is_crash_avg_speed_range_intent(text):
        return "crash", build_crash_avg_speed_range_steps(text), dataset_out

    if is_avg_speed_near_intent(text):
        return "traffic", build_avg_speed_near_steps(text), dataset_out

    if is_avg_speed_range_intent(text):
        return "traffic", build_avg_speed_range_steps(text), dataset_out

    if is_speed_limit_range_intent(text):
        return "traffic", build_speed_limit_range_steps(text), dataset_out

    if is_speed_limit_roads_intent(text):
        return "traffic", build_speed_limit_roads_steps(text), dataset_out

    if is_crash_corridor_datetime_intent(text):
        return "crash", build_crash_corridor_datetime_steps(text), dataset_out

    if is_crash_time_window_intent(text):
        corridor_conditions_dt = build_corridor_filter_conditions(text, refs)
        steps_tw = build_crash_time_window_steps(text)
        if corridor_conditions_dt:
            steps_tw = _merge_filter_conditions(
                steps_tw, corridor_conditions_dt, mode="or"
            )
        return "crash", steps_tw, dataset_out

    # Crash corridor / show crashes
    if is_crash_query(text) or domain_out in {"crash", "crashes", "event", "events"}:
        domain_out = "crash"
        if time_conditions and not _steps_have_time_filter(steps_out):
            steps_out = _merge_filter_conditions(steps_out, time_conditions, mode="and")
        if corridor_conditions and not _steps_have_road_filter(steps_out):
            steps_out = _merge_filter_conditions(steps_out, corridor_conditions, mode="or")
        if wants_map and not _has_operation(steps_out, "generate_map"):
            steps_out.append(
                {
                    "operation": "generate_map",
                    "params": {"limit": 10000, "per_cell": 6},
                }
            )
        if "how many" in lowered and not _has_operation(steps_out, "aggregate"):
            steps_out = build_crash_count_steps(text, with_map=wants_map and _has_operation(steps_out, "generate_map"))
            return domain_out, steps_out, dataset_out

    # Traffic corridor highlight on map
    elif domain_out in {"traffic", "cv"} or (is_traffic_query(text) and corridor_conditions):
        domain_out = "traffic"
        if corridor_conditions and not _steps_have_road_filter(steps_out):
            steps_out = _merge_filter_conditions(steps_out, corridor_conditions, mode="or")
        if wants_map and not _has_operation(steps_out, "generate_map"):
            steps_out.append({"operation": "generate_map", "params": {"limit": 5000}})

    # Named CV road (e.g. "show US 6 E") — highlight corridor on map
    elif is_show_named_road_intent(text):
        domain_out = "traffic"
        steps_out = build_named_road_steps(text)

    # All hard-braking roads (CV aggregates; no point table required)
    elif is_show_all_hard_braking_intent(text):
        domain_out = "traffic"
        steps_out = build_show_all_hard_braking_steps()

    return domain_out, steps_out, dataset_out


def _steps_have_time_filter(steps: List[dict]) -> bool:
    time_cols = {"local_time", "local_hour", "accident_time", "event_time", "crash_time"}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "").strip().lower() != "filter":
            continue
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        for cond in params.get("conditions") or []:
            if not isinstance(cond, dict):
                continue
            col = str(cond.get("column") or "").strip().lower()
            if col in time_cols:
                return True
    return False


__all__ = [
    "build_corridor_filter_conditions",
    "build_crash_count_steps",
    "build_crash_corridor_datetime_steps",
    "build_crash_avg_speed_range_steps",
    "build_crash_speed_limit_steps",
    "build_crash_time_window_steps",
    "build_avg_speed_near_steps",
    "build_avg_speed_range_steps",
    "build_speed_limit_range_steps",
    "build_speed_limit_roads_steps",
    "build_top_crash_segments_steps",
    "build_named_road_steps",
    "build_show_all_hard_braking_steps",
    "corridor_ilike_patterns",
    "extract_county_road_label",
    "extract_named_road_label",
    "extract_show_road_label",
    "extract_route_refs",
    "extract_accident_date",
    "query_uses_placeholder_datetime",
    "PLACEHOLDER_DATETIME_NOTE",
    "extract_time_of_day_window",
    "extract_top_n",
    "is_crash_count_intent",
    "is_sparse_crash_interval_intent",
    "extract_crash_count_threshold",
    "is_crash_corridor_datetime_intent",
    "is_crash_query",
    "is_crash_speed_limit_intent",
    "is_crash_time_window_intent",
    "extract_speed_limit_range",
    "query_mentions_avg_speed",
    "is_avg_speed_near_intent",
    "is_avg_speed_range_intent",
    "is_crash_avg_speed_range_intent",
    "is_speed_limit_range_intent",
    "is_speed_limit_roads_intent",
    "is_top_crash_segments_intent",
    "is_map_rerun_intent",
    "is_show_all_hard_braking_intent",
    "is_show_named_road_intent",
    "is_traffic_query",
    "normalize_execute_query_plan",
    "query_explicitly_table_only",
    "query_requests_map",
    "sanitize_query_text",
]

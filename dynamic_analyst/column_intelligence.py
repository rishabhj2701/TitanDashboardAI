# dynamic_analyst/column_intelligence.py
"""
Column Intelligence Module
Provides semantic matching, aliases, and smart column discovery for agents.
"""

from typing import Dict, List, Optional, Set, Tuple
from difflib import SequenceMatcher

# Semantic keyword mappings: concept -> related terms
SEMANTIC_KEYWORDS = {
    # Fatality/death related
    "fatality": ["killed", "death", "fatal", "deceased", "fatalities", "deaths"],
    "killed": ["fatality", "death", "fatal", "deceased", "fatalities", "deaths"],

    # Injury related
    "injury": ["injured", "hurt", "wound", "injuries", "harm"],
    "injured": ["injury", "hurt", "wound", "injuries", "harm"],

    # Person types
    "pedestrian": ["ped", "walker", "foot", "pedestrians"],
    "cyclist": ["bike", "bicycle", "cycling", "biker", "cyclists"],
    "motorist": ["driver", "vehicle", "car", "motor", "motorists"],
    "passenger": ["occupant", "rider", "passengers"],

    # Severity
    "fatal": ["fatality", "death", "killed", "deadly"],
    "serious": ["severe", "major", "critical", "serious_injury"],
    "minor": ["slight", "light", "minor_injury"],

    # Location
    "road": ["street", "highway", "route", "roadway", "travelway"],
    "intersection": ["junction", "crossing", "intersect"],

    # Time
    "date": ["day", "time", "when", "datetime", "timestamp"],
    "time": ["hour", "when", "datetime", "timestamp"],

    # Counts
    "count": ["number", "num", "total", "quantity", "no_of"],
    "total": ["sum", "all", "count", "number"],
}

# Category keywords for column classification
CATEGORY_KEYWORDS = {
    "fatality": ["killed", "death", "fatal", "deceased"],
    "injury": ["injured", "injury", "hurt", "wound"],
    "pedestrian": ["pedestrian", "ped", "walker"],
    "cyclist": ["cyclist", "bike", "bicycle"],
    "vehicle": ["vehicle", "car", "motorist", "driver"],
    "location": ["lat", "lon", "longitude", "latitude", "road", "street", "location"],
    "time": ["date", "time", "timestamp", "day", "month", "year", "hour"],
    "severity": ["severity", "fatal", "serious", "minor", "critical"],
    "weather": ["weather", "rain", "snow", "fog", "clear"],
    "count": ["num", "number", "count", "total", "no_of"],
}


def _normalize(text: str) -> str:
    """Normalize text for matching: lowercase, replace separators with underscores."""
    return text.lower().replace(" ", "_").replace("-", "_")


def _tokenize(text: str) -> Set[str]:
    """Split text into tokens."""
    normalized = _normalize(text)
    # Split on underscores and get individual words
    tokens = set(normalized.split("_"))
    tokens.add(normalized)  # Also add the full normalized string
    return tokens


def _similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def suggest_similar_columns(
    failed_column: str,
    available_columns: Set[str],
    max_suggestions: int = 5
) -> List[Tuple[str, float]]:
    """
    Suggest similar columns when a query fails.

    Args:
        failed_column: The column name that wasn't found
        available_columns: Set of actual column names
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of (column_name, similarity_score) tuples, sorted by score
    """
    suggestions = []

    for col in available_columns:
        # Calculate similarity
        sim = _similarity(failed_column, col)

        # Boost if tokens overlap
        failed_tokens = _tokenize(failed_column)
        col_tokens = _tokenize(col)
        common = failed_tokens & col_tokens
        if common:
            sim += 0.1 * len(common)

        # Boost if semantic keywords match
        for token in failed_tokens:
            synonyms = set(SEMANTIC_KEYWORDS.get(token, []))
            if synonyms & col_tokens:
                sim += 0.15

        suggestions.append((col, min(sim, 1.0)))

    # Sort by score descending
    suggestions.sort(key=lambda x: x[1], reverse=True)
    return suggestions[:max_suggestions]


def categorize_columns(columns: Set[str]) -> Dict[str, List[str]]:
    """
    Categorize columns by their likely purpose.

    Args:
        columns: Set of column names

    Returns:
        Dict mapping category -> list of column names
    """
    categories = {
        "fatality": [],
        "injury": [],
        "location": [],
        "time": [],
        "severity": [],
        "categorical": [],
        "numeric": [],
        "other": [],
    }

    categorized = set()

    for col in columns:
        col_lower = col.lower()

        # Check each category
        for category, keywords in CATEGORY_KEYWORDS.items():
            if category not in categories:
                continue
            for kw in keywords:
                if kw in col_lower:
                    if category in categories:
                        categories[category].append(col)
                        categorized.add(col)
                    break

    # Put uncategorized columns in "other"
    for col in columns:
        if col not in categorized:
            categories["other"].append(col)

    # Remove empty categories
    return {k: v for k, v in categories.items() if v}


def generate_query_suggestions(
    columns: Set[str],
    column_profiles: Optional[Dict] = None
) -> List[str]:
    """
    Generate suggested queries based on available columns.

    Args:
        columns: Set of column names
        column_profiles: Optional dict of column metadata

    Returns:
        List of suggested query strings
    """
    suggestions = []
    categorized = categorize_columns(columns)

    # Fatality queries
    fatality_cols = categorized.get("fatality", [])
    if fatality_cols:
        suggestions.append("How many total fatalities occurred?")
        for col in fatality_cols:
            if "pedestrian" in col.lower():
                suggestions.append("How many pedestrian fatalities occurred?")
            elif "cyclist" in col.lower():
                suggestions.append("How many cyclist fatalities occurred?")

    # Injury queries
    injury_cols = categorized.get("injury", [])
    if injury_cols:
        suggestions.append("How many injuries occurred?")

    # Severity queries
    severity_cols = categorized.get("severity", [])
    if severity_cols:
        suggestions.append("Show crashes by severity")
        suggestions.append("How many fatal crashes occurred?")

    # Location queries
    location_cols = categorized.get("location", [])
    if location_cols:
        road_cols = [c for c in location_cols if any(k in c.lower() for k in ["road", "street", "route"])]
        if road_cols:
            suggestions.append("Which roads have the most crashes?")
            suggestions.append("Show crashes on [road name]")

    # Time queries
    time_cols = categorized.get("time", [])
    if time_cols:
        suggestions.append("Show crashes by month")
        suggestions.append("When do most crashes occur?")

    # Map queries
    if any("lat" in c.lower() or "lon" in c.lower() for c in columns):
        suggestions.append("Show all crashes on map")

    return suggestions[:10]  # Limit to 10 suggestions

def generate_data_awareness_context(
    dataset_id: str,
    dataset_name: str,
    entity_type: str,
    row_count: int,
    columns: Set[str],
    column_profiles: Optional[Dict] = None
) -> str:
    """
    Generate a concise data awareness context for agent prompts.
    This helps agents know what data is available BEFORE querying.

    Args:
        dataset_id: The dataset identifier
        dataset_name: Human-readable dataset name
        entity_type: Type of data (crash, workzone, etc.)
        row_count: Number of rows in dataset
        columns: Set of column names
        column_profiles: Optional dict of column metadata

    Returns:
        Formatted context string for agent injection
    """
    categorized = categorize_columns(columns)

    lines = [
        f"=== ACTIVE DATASET: {dataset_name} ({entity_type}) ===",
        f"Dataset ID: {dataset_id}",
        f"Total Records: {row_count:,}",
        "",
        "AVAILABLE COLUMNS BY CATEGORY:",
    ]

    # Key categories with their columns
    category_info = [
        ("Fatality/Death", categorized.get("fatality", []), "Use for: counting deaths, filtering fatal crashes"),
        ("Injury", categorized.get("injury", []), "Use for: counting injuries, filtering by injury type"),
        ("Severity", categorized.get("severity", []), "Use for: grouping by severity, filtering fatal/serious"),
        ("Location", categorized.get("location", []), "Use for: filtering by road/city, mapping"),
        ("Time", categorized.get("time", []), "Use for: time-based filtering, trends by date/month"),
    ]

    for cat_name, cols, usage in category_info:
        if cols:
            col_list = ", ".join(cols[:5])
            if len(cols) > 5:
                col_list += f" (+{len(cols)-5} more)"
            lines.append(f"  {cat_name}: {col_list}")
            lines.append(f"    → {usage}")

    # Show categorical columns with their values (for filtering/grouping)
    if column_profiles:
        lines.append("")
        lines.append("CATEGORICAL COLUMNS (good for filtering/grouping):")
        for col, profile in column_profiles.items():
            unique_vals = profile.get("unique_values")
            if unique_vals and len(unique_vals) <= 15:
                vals_str = ", ".join(str(v) for v in unique_vals[:8])
                if len(unique_vals) > 8:
                    vals_str += f" (+{len(unique_vals)-8} more)"
                lines.append(f"  {col}: [{vals_str}]")

    # Show numeric columns (for aggregation)
    numeric_cols = []
    for col in columns:
        col_lower = col.lower()
        if any(k in col_lower for k in ("num", "count", "number", "killed", "injured", "total", "no_of", "speed", "limit")):
            numeric_cols.append(col)

    if numeric_cols:
        lines.append("")
        lines.append("NUMERIC COLUMNS (good for SUM/AVG/COUNT):")
        lines.append(f"  {', '.join(numeric_cols[:10])}")

    # Query capabilities
    lines.append("")
    lines.append("YOU CAN ANSWER QUESTIONS LIKE:")
    suggestions = generate_query_suggestions(columns, column_profiles)
    for suggestion in suggestions[:5]:
        lines.append(f"  • {suggestion}")

    lines.append("")
    lines.append("=== END DATASET CONTEXT ===")

    return "\n".join(lines)

"""Code lookup helpers.

Static hard-coded lookups are intentionally disabled.
Code translation should come only from uploaded codebook mappings.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# Static lookup map — Iowa KABCO severity scale for CSEVERITY column
COLUMN_CODE_LOOKUPS: Dict[str, Dict[str, str]] = {
    # Iowa KABCO severity scale (used in CSEVERITY column)
    "cseverity": {
        "1": "Fatal",
        "2": "Major Injury",
        "3": "Minor Injury",
        "4": "Property Damage Only",
        "5": "Unknown / Not Reported",
    },
    "CSEVERITY": {
        "1": "Fatal",
        "2": "Major Injury",
        "3": "Minor Injury",
        "4": "Property Damage Only",
        "5": "Unknown / Not Reported",
    },
    "severity": {
        "1": "Fatal",
        "2": "Major Injury",
        "3": "Minor Injury",
        "4": "Property Damage Only",
        "5": "Unknown / Not Reported",
    },
}



def get_code_description(column_name: str, code_value: Any) -> Optional[str]:
    """Return a human-readable description for a coded value, if available."""
    lookup_table = COLUMN_CODE_LOOKUPS.get(column_name.lower())
    if not lookup_table:
        return None
    code_str = str(code_value).strip()
    return lookup_table.get(code_str) or lookup_table.get(code_str.upper())


def translate_code_value(column_name: str, code_value: Any) -> str:
    """Translate a code to its description, or return the original value as text."""
    description = get_code_description(column_name, code_value)
    if description:
        return f"{code_value} ({description})"
    return str(code_value)


def has_code_lookup(column_name: str) -> bool:
    """Check whether the given column has an active static lookup."""
    return column_name.lower() in COLUMN_CODE_LOOKUPS


def get_lookup_table(column_name: str) -> Optional[Dict[str, str]]:
    """Get the static lookup table for a column, if one exists."""
    return COLUMN_CODE_LOOKUPS.get(column_name.lower())


def translate_results(
    results: List[Dict[str, Any]],
    columns_to_translate: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Translate coded values in a list of row dicts."""
    if not results:
        return results

    translated = []
    for row in results:
        new_row = {}
        for col, val in row.items():
            if columns_to_translate and col not in columns_to_translate:
                new_row[col] = val
            elif has_code_lookup(col) and val is not None:
                desc = get_code_description(col, val)
                if desc:
                    new_row[col] = desc
                    new_row[f"{col}_code"] = val
                else:
                    new_row[col] = val
            else:
                new_row[col] = val
        translated.append(new_row)
    return translated


def get_code_summary(column_name: str) -> str:
    """Return a printable summary of static lookups for a column."""
    lookup = get_lookup_table(column_name)
    if not lookup:
        return f"No code lookup available for '{column_name}'"
    lines = [f"Code lookup for '{column_name}':"]
    for code, desc in sorted(lookup.items(), key=lambda item: (len(item[0]), item[0])):
        lines.append(f"  {code}: {desc}")
    return "\n".join(lines)


def list_available_lookups() -> str:
    """Return all columns that currently have a static lookup table."""
    if not COLUMN_CODE_LOOKUPS:
        return "No static code lookups are enabled."
    return ", ".join(sorted(COLUMN_CODE_LOOKUPS.keys()))


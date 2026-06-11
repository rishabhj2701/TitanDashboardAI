from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional


# Single source of truth for Ingestion Inspector entity behavior.
# Add new entities here as the product expands.
ENTITY_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "crash": {
        "label": "Crash",
        "required_fields": ["event_date", "event_time", "latitude", "longitude"],
        "optional_fields": ["primary_id", "timestamp", "geometry", "road_name", "road_id"],
        "description": "Crash incident records with date/time and location.",
    },
    "workzone": {
        "label": "Work Zone",
        "required_fields": ["start_time", "end_time", "road_name"],
        "optional_fields": ["primary_id", "timestamp", "latitude", "longitude", "geometry", "road_id"],
        "description": "Work zone records with start/end timing and road context.",
    },
    "event": {
        "label": "Generic Event",
        "required_fields": ["timestamp", "latitude", "longitude"],
        "optional_fields": ["primary_id", "event_date", "event_time", "geometry", "road_name", "road_id"],
        "description": "Generic event records for flexible datasets.",
    },
}


DEFAULT_ENTITY_TYPE = "event"


def normalize_entity_type(entity_type: Optional[str]) -> str:
    key = str(entity_type or "").strip().lower()
    if key in ENTITY_SCHEMAS:
        return key
    if key in {"events", "generic", "other"}:
        return DEFAULT_ENTITY_TYPE
    return DEFAULT_ENTITY_TYPE


def get_entity_schema(entity_type: Optional[str]) -> Dict[str, Any]:
    key = normalize_entity_type(entity_type)
    schema = deepcopy(ENTITY_SCHEMAS.get(key, ENTITY_SCHEMAS[DEFAULT_ENTITY_TYPE]))
    schema["entity_type"] = key
    return schema


def get_supported_entity_schemas() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for key, schema in ENTITY_SCHEMAS.items():
        item = deepcopy(schema)
        item["entity_type"] = key
        out[key] = item
    return out


def compute_mapping_status(
    entity_type: Optional[str],
    mapping_fields: Optional[Dict[str, Any]],
    *,
    available_columns: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    schema = get_entity_schema(entity_type)
    required_fields: List[str] = list(schema.get("required_fields") or [])
    optional_fields: List[str] = list(schema.get("optional_fields") or [])
    fields = mapping_fields if isinstance(mapping_fields, dict) else {}
    normalized_fields: Dict[str, str] = {}
    for key, value in fields.items():
        text = str(value).strip() if isinstance(value, str) else ""
        if text:
            normalized_fields[str(key)] = text
    scoped_keys = set(required_fields + optional_fields)
    scoped_fields = {key: value for key, value in normalized_fields.items() if key in scoped_keys}

    missing_required = [field for field in required_fields if not scoped_fields.get(field)]
    mapped_required = [field for field in required_fields if scoped_fields.get(field)]
    mapped_optional = [field for field in optional_fields if scoped_fields.get(field)]
    mapped_total = len(scoped_fields)
    expected_total = len(set(required_fields + optional_fields))

    warnings: List[str] = []
    if missing_required:
        warnings.append(
            "Missing required mappings for "
            + schema.get("label", "this entity")
            + f": {', '.join(missing_required)}. Analysis can still run but may be less accurate."
        )

    if available_columns is not None:
        known_cols = {str(col).strip() for col in available_columns if str(col).strip()}
        known_cols_lower = {col.lower() for col in known_cols}

        def _source_exists(source: str) -> bool:
            candidate = str(source).strip()
            if not candidate:
                return False
            if candidate in known_cols or candidate.lower() in known_cols_lower:
                return True

            # Derived timestamp mappings may be stored as "<date_col>+<time_col>".
            if "+" in candidate:
                parts = [part.strip() for part in candidate.split("+") if part and part.strip()]
                if len(parts) >= 2 and all(
                    (part in known_cols) or (part.lower() in known_cols_lower) for part in parts
                ):
                    return True
            return False

        stale_mappings = sorted(
            [
                f"{field} -> {source}"
                for field, source in scoped_fields.items()
                if not _source_exists(source)
            ]
        )
        if stale_mappings:
            warnings.append(
                "Some mapped columns are not present in this dataset: "
                + ", ".join(stale_mappings[:6])
                + ("..." if len(stale_mappings) > 6 else "")
            )

    if missing_required:
        level = "missing_required"
        message = "Missing required mappings."
    elif mapped_required and mapped_total >= max(1, len(required_fields)):
        level = "ready"
        message = "Required mappings are set."
    elif mapped_total > 0:
        level = "partial"
        message = "Partially mapped."
    else:
        level = "unmapped"
        message = "No mappings set."

    return {
        "entity_type": schema.get("entity_type"),
        "entity_label": schema.get("label"),
        "required_fields": required_fields,
        "optional_fields": optional_fields,
        "missing_required": missing_required,
        "mapped_required": mapped_required,
        "mapped_optional": mapped_optional,
        "mapped_total": mapped_total,
        "expected_total": expected_total,
        "completion_ratio": round(float(mapped_total / expected_total), 4) if expected_total else 0.0,
        "level": level,
        "message": message,
        "warnings": warnings,
    }

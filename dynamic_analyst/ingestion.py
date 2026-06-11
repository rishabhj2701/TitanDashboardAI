# dynamic_analyst/ingestion.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .ingestion_profile import (
    _detect_geo_columns,
    _explain_entity_guess,
    _infer_capabilities,
    _infer_entity_type,
    _infer_mapping_fields,
)

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
IN_MEMORY_DISABLED_MESSAGE = "In-memory dataset registry is disabled. Use /api/upload or PostGIS ingestion."


def _require_upload_path(filepath: str) -> Path:
    uploads_dir = UPLOADS_DIR.resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)

    path = Path(filepath).expanduser()
    if not path.is_absolute():
        path = (uploads_dir / path).resolve()
    else:
        path = path.resolve()

    if uploads_dir not in path.parents and path != uploads_dir:
        raise ValueError(f"File must be inside uploads directory: {uploads_dir}")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Upload not found: {path}")
    return path


def profile_incoming_file(filepath: str) -> str:
    """Read the start of a file and generate a concise schema/profile summary."""
    try:
        file_path = _require_upload_path(filepath)
        df = pd.read_csv(file_path, nrows=50)

        profile = [f"File Analysis for: {file_path}"]
        profile.append(f"Total Columns: {len(df.columns)}")
        profile.append("Column Details:")

        for col in df.columns:
            series = df[col].dropna()
            example = series.iloc[0] if not series.empty else "All Null"
            profile.append(f" - '{col}' (Type: {df[col].dtype}) | Example: {example}")

        lat_col, lon_col, geom_col = _detect_geo_columns(df)
        quick_meta = {
            "geo": {
                "lat_column": lat_col,
                "lon_column": lon_col,
                "geometry_column": geom_col,
            },
            "road_match": {},
        }

        entity_type = _infer_entity_type(file_path.stem, list(df.columns))
        mapping_fields = _infer_mapping_fields(df, quick_meta, entity_type=entity_type)
        mapping = {"entity_type": entity_type, "fields": mapping_fields}
        capabilities = _infer_capabilities(entity_type, mapping, quick_meta)
        matched_cols = _explain_entity_guess(entity_type, list(df.columns))

        if entity_type and entity_type != "other":
            hint = f"Heuristic Guess: {entity_type}"
            if matched_cols:
                hint += f" (matched columns: {matched_cols})"
            profile.append(hint)

        if capabilities:
            profile.append(f"Capabilities: {', '.join(capabilities)}")

        mapped_pairs = [f"{field} -> {source}" for field, source in mapping_fields.items() if source]
        if mapped_pairs:
            profile.append("Suggested Mapping:")
            for pair in mapped_pairs:
                profile.append(f" - {pair}")

        return "\n".join(profile)
    except Exception as e:
        return f"Error profiling file: {e}"


def register_new_dataset(filepath: str, dataset_name: str) -> str:
    """Legacy in-memory registration endpoint (disabled)."""
    return f"❌ ERROR: {IN_MEMORY_DISABLED_MESSAGE}"


def register_dataset_from_bytes(
    filename: str,
    content: bytes,
    dataset_name: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    raise ValueError(IN_MEMORY_DISABLED_MESSAGE)


def register_dataset_from_url(url: str, dataset_name: Optional[str] = None) -> dict:
    raise ValueError(IN_MEMORY_DISABLED_MESSAGE)


def apply_dataset_mapping(dataset_name: str, target_name: Optional[str] = None) -> dict:
    raise ValueError(IN_MEMORY_DISABLED_MESSAGE)

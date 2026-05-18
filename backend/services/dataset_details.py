from __future__ import annotations

from typing import Any, Dict, Tuple

from backend.services.ingestion_entities import (
    compute_mapping_status,
    get_supported_entity_schemas,
    normalize_entity_type,
)
from dynamic_analyst.demo_queryable_defaults import merge_iowa_queryable_fields
from dynamic_analyst.queryable_fields import resolve_queryable_fields

_EVENT_ENTITY_TYPES = ("crash", "event", "workzone", "signal")


def _default_store():
    from dynamic_analyst import postgis_store

    return postgis_store


def _normalize_detail_defaults(ds: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    stats = ds.get("stats") if isinstance(ds.get("stats"), dict) else {}
    ingest = stats.get("ingest") if isinstance(stats.get("ingest"), dict) else {}
    mapping_fields = ingest.get("mapping_fields") if isinstance(ingest.get("mapping_fields"), dict) else {}

    if "row_count" not in ds:
        ds["row_count"] = ingest.get("rows_inserted") or ingest.get("rows")
    if "road_match" not in ds:
        ds["road_match"] = stats.get("road_match")
    if isinstance(ds.get("road_match"), dict):
        if "road_segment_id_column" not in ds["road_match"]:
            ds["road_match"]["road_segment_id_column"] = "road_segment_id"
        if "road_column" not in ds["road_match"]:
            ds["road_match"]["road_column"] = mapping_fields.get("road_name") or "road"
    if "ingested_at" not in ds and ds.get("created_at"):
        ds["ingested_at"] = ds["created_at"]
    if "dataset" not in ds and ds.get("name"):
        ds["dataset"] = ds["name"]

    mapping = ds.get("mapping") if isinstance(ds.get("mapping"), dict) else None
    if not mapping and mapping_fields:
        ds["mapping"] = {
            "entity_type": ds.get("entity_type"),
            "fields": mapping_fields,
            "auto": True,
            "updated_at": ds.get("created_at"),
        }
    elif mapping and "fields" not in mapping:
        mapping["fields"] = {}

    if "codebook" not in ds and isinstance(stats.get("codebook"), dict):
        ds["codebook"] = stats.get("codebook")
    if "codebook" not in ds or not isinstance(ds.get("codebook"), dict):
        ds["codebook"] = {}
    if "queryable_fields" not in ds and isinstance(stats.get("queryable_fields"), dict):
        ds["queryable_fields"] = stats.get("queryable_fields")

    if "geo" not in ds:
        ds["geo"] = {
            "lat_column": ingest.get("lat_col"),
            "lon_column": ingest.get("lon_col"),
        }
    return ds, stats, ingest, mapping_fields


def build_dataset_detail(dataset_id: str, store=None) -> dict[str, Any]:
    if store is None:
        store = _default_store()

    ds = store.get_dataset(dataset_id)
    ds, stats, ingest, _mapping_fields = _normalize_detail_defaults(ds)

    try:
        codebook_attributes = sorted(store.list_codebook_attributes())
    except Exception:
        codebook_attributes = []
    ds["codebook_attributes"] = codebook_attributes

    if ds["codebook_attributes"] and ds.get("entity_type") in _EVENT_ENTITY_TYPES:
        current_mappings = ds["codebook"].get("mappings") if isinstance(ds["codebook"], dict) else {}
        if not isinstance(current_mappings, dict) or not current_mappings:
            try:
                store.refresh_dataset_codebook_info(dataset_id, sample_rows=3000)
                ds = store.get_dataset(dataset_id)
                ds, stats, ingest, _mapping_fields = _normalize_detail_defaults(ds)
                ds["codebook_attributes"] = codebook_attributes
            except Exception:
                pass

    if isinstance(ds.get("codebook"), dict):
        ds["codebook"]["available"] = bool(codebook_attributes)
        if "attributes" not in ds["codebook"]:
            ds["codebook"]["attributes"] = len(codebook_attributes)

    if ds.get("entity_type") in _EVENT_ENTITY_TYPES:
        preview = store.preview_events(dataset_id, limit=6)
        flattened = []
        for row in preview:
            props = row.pop("props", None)
            if isinstance(props, dict):
                for key, value in props.items():
                    if key not in row:
                        row[key] = value
            flattened.append(row)
        ds["preview_rows"] = flattened

        if "columns" not in ds:
            base_cols = ["ts", "lat", "lon", "road_segment_id", "road_dist_m", "road_conf"]
            cols = set(base_cols)
            for row in flattened:
                cols.update(row.keys())
            ordered = base_cols + [col for col in sorted(cols) if col not in base_cols]
            ds["columns"] = ordered

    mapping = ds.get("mapping") if isinstance(ds.get("mapping"), dict) else {}
    mapping_fields = mapping.get("fields") if isinstance(mapping.get("fields"), dict) else {}
    entity_type = normalize_entity_type(mapping.get("entity_type") or ds.get("entity_type"))
    mapping_status = compute_mapping_status(
        entity_type,
        mapping_fields,
        available_columns=(ds.get("columns") if isinstance(ds.get("columns"), list) else None),
    )
    ds["mapping_status"] = mapping_status
    ds["entity_schemas"] = get_supported_entity_schemas()
    if isinstance(mapping, dict):
        mapping["entity_type"] = entity_type
        ds["mapping"] = mapping

    stored_queryable = ds.get("queryable_fields") if isinstance(ds.get("queryable_fields"), dict) else {}
    import_method = ""
    if isinstance(stats, dict):
        import_method = str(stats.get("import_method") or "")
    if entity_type in {"crash", "event", "cv"} or "iowa" in import_method.lower():
        stored_queryable = merge_iowa_queryable_fields(entity_type, stored_queryable)
    queryable_fields = resolve_queryable_fields(
        entity_type,
        stored_queryable,
        available_sources=(ds.get("columns") if isinstance(ds.get("columns"), list) else None),
    )
    ds["queryable_fields"] = {
        "entity_type": entity_type,
        "fields": queryable_fields,
        "updated_at": (
            stored_queryable.get("updated_at")
            if isinstance(stored_queryable, dict)
            else None
        ) or ds.get("created_at"),
    }

    warnings = list(ds.get("warnings") or [])
    warnings.extend(mapping_status.get("warnings") or [])
    if warnings:
        ds["warnings"] = list(dict.fromkeys([str(item).strip() for item in warnings if str(item).strip()]))

    return ds

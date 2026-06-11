from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.services.ingestion_entities import normalize_entity_type
from dynamic_analyst.queryable_fields import resolve_queryable_fields
from dynamic_analyst import postgis_store
from dynamic_analyst.data_registry import registry
from dynamic_analyst.session_state import clear_execution_for_session, set_active_session
from dynamic_analyst.pipeline_react import clear_schema_cache_for_session
from backend.services.dataset_details import build_dataset_detail

router = APIRouter()


class MappingRequest(BaseModel):
    entity_type: Optional[str] = None
    fields: Optional[Dict[str, Optional[str]]] = None


class CodeMappingRequest(BaseModel):
    mappings: Optional[Dict[str, Optional[str]]] = None


class QueryableFieldRequest(BaseModel):
    query_name: Optional[str] = None
    source_column: Optional[str] = None
    enabled: Optional[bool] = True


class QueryableFieldsUpdateRequest(BaseModel):
    fields: Optional[List[QueryableFieldRequest]] = None


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id header")
    set_active_session(x_session_id)
    return x_session_id


@router.post("/api/datasets/{dataset_name}/mapping")
def update_dataset_mapping(
    dataset_name: str,
    payload: MappingRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    dataset_id = registry.resolve_dataset_name(dataset_name) or dataset_name
    try:
        meta = registry.get_metadata(dataset_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found.")
    if not isinstance(meta, dict):
        meta = {}

    fields = payload.fields
    sanitized_fields = {
        key: value.strip() if isinstance(value, str) and value.strip() else None
        for key, value in (fields or {}).items()
    }
    existing_mapping = meta.get("mapping") if isinstance(meta.get("mapping"), dict) else {}
    existing_fields = existing_mapping.get("fields") if isinstance(existing_mapping.get("fields"), dict) else {}
    merged_fields = dict(existing_fields)
    if fields is not None:
        for key, value in sanitized_fields.items():
            merged_fields[key] = value
    resolved_entity_type = normalize_entity_type(
        payload.entity_type
        if payload.entity_type is not None
        else (existing_mapping.get("entity_type") or meta.get("entity_type"))
    )
    mapping = {
        "entity_type": resolved_entity_type,
        "fields": merged_fields,
        "auto": False,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    registry.update_metadata(dataset_id, {"mapping": mapping})
    return build_dataset_detail(dataset_id)


@router.post("/api/datasets/{dataset_name}/queryable-fields")
def update_dataset_queryable_fields(
    dataset_name: str,
    payload: QueryableFieldsUpdateRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    dataset_id = registry.resolve_dataset_name(dataset_name) or dataset_name
    try:
        meta = registry.get_metadata(dataset_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found.")
    if not isinstance(meta, dict):
        meta = {}

    incoming_fields = payload.fields
    if incoming_fields is None:
        raise HTTPException(status_code=400, detail="Request must include a 'fields' array.")

    mapping = meta.get("mapping") if isinstance(meta.get("mapping"), dict) else {}
    entity_type = normalize_entity_type(mapping.get("entity_type") or meta.get("entity_type"))
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    existing = stats.get("queryable_fields") if isinstance(stats.get("queryable_fields"), dict) else {}
    available_columns = meta.get("columns") if isinstance(meta.get("columns"), list) else None

    normalized = resolve_queryable_fields(
        entity_type,
        {
            **existing,
            "fields": [
                {
                    "query_name": field.query_name,
                    "source_column": field.source_column,
                    "enabled": bool(field.enabled) if field.enabled is not None else True,
                }
                for field in incoming_fields
            ],
        },
        available_sources=available_columns,
    )

    registry.update_metadata(
        dataset_id,
        {
            "queryable_fields": {
                "entity_type": entity_type,
                "fields": normalized,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        },
    )
    # Avoid stale blocked-plan context on the next chat turn after queryable policy changes.
    clear_execution_for_session(x_session_id)
    clear_schema_cache_for_session(session_id=x_session_id)
    return build_dataset_detail(dataset_id)


@router.post("/api/datasets/{dataset_name}/code-mappings")
def update_dataset_code_mappings(
    dataset_name: str,
    payload: CodeMappingRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    _require_session(x_session_id)
    dataset_id = registry.resolve_dataset_name(dataset_name) or dataset_name
    try:
        meta = registry.get_metadata(dataset_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found.")

    stats = meta.get("stats") if isinstance(meta, dict) else {}
    if not isinstance(stats, dict):
        stats = {}
    codebook_info = stats.get("codebook") if isinstance(stats.get("codebook"), dict) else {}
    existing = codebook_info.get("mappings") if isinstance(codebook_info.get("mappings"), dict) else {}

    try:
        available_attrs = {str(attr).upper() for attr in postgis_store.list_codebook_attributes()}
    except Exception:
        available_attrs = set()
    if not available_attrs:
        raise HTTPException(status_code=400, detail="No codebook attributes are available in this session.")

    merged: Dict[str, Dict[str, Any]] = {}
    for col, info in existing.items():
        if isinstance(info, dict) and info.get("attr"):
            merged[str(col)] = dict(info)
        elif isinstance(info, str) and info.strip():
            merged[str(col)] = {"attr": info.strip().upper(), "method": "manual"}

    requested = payload.mappings or {}
    for col, raw_attr in requested.items():
        col_name = str(col or "").strip()
        if not col_name:
            continue
        attr_text = raw_attr.strip().upper() if isinstance(raw_attr, str) and raw_attr.strip() else ""
        if not attr_text:
            merged.pop(col_name, None)
            continue
        if attr_text not in available_attrs:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown codebook attribute '{attr_text}' for column '{col_name}'.",
            )
        values = postgis_store.get_dataset_distinct_values(dataset_id, col_name, limit=120)
        lookup = postgis_store.get_codebook_map_for_columns([attr_text]).get(attr_text, {})
        mapped_values = [value for value in values if value in lookup or (value.lstrip("0") or "0") in lookup]
        sample_pairs = []
        for code in mapped_values[:8]:
            key = code if code in lookup else (code.lstrip("0") or "0")
            sample_pairs.append({"code": code, "label": lookup.get(key) or lookup.get(code) or code})
        total = len(values)
        mapped = len(mapped_values)
        coverage = float(mapped / total) if total else 0.0
        merged[col_name] = {
            "attr": attr_text,
            "method": "manual",
            "confidence": round(min(1.0, 0.5 + (0.5 * coverage)), 3),
            "unique_values": total,
            "mapped_unique_values": mapped,
            "coverage": round(coverage, 4),
            "sample_labels": sample_pairs,
            "sample_values": values[:8],
        }

    existing_candidates = (
        codebook_info.get("candidates") if isinstance(codebook_info.get("candidates"), list) else []
    )
    candidates = sorted({*existing_candidates, *merged.keys()})
    matches = sorted(merged.keys())
    missing = sorted([candidate for candidate in candidates if candidate not in set(matches)])

    updated_codebook = dict(codebook_info)
    updated_codebook.update(
        {
            "available": bool(available_attrs),
            "attributes": len(available_attrs),
            "candidates": candidates,
            "matches": matches,
            "missing": missing,
            "mappings": merged,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
    )

    registry.update_metadata(dataset_id, {"codebook": updated_codebook})
    return build_dataset_detail(dataset_id)

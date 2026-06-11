from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from backend.services.dataset_details import build_dataset_detail
from dynamic_analyst import postgis_store
from dynamic_analyst.session_state import get_active_user

router = APIRouter()
logger = logging.getLogger("adk_server")


@router.get("/api/datasets-id/{dataset_id}")
def dataset_detail_by_id(dataset_id: str):
    try:
        payload = build_dataset_detail(dataset_id)
        logger.info(
            json.dumps(
                {
                    "event": "audit_dataset_detail_access",
                    "dataset_id": dataset_id,
                    "user_id": get_active_user(),
                    "status": "success",
                }
            )
        )
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        logger.info(
            json.dumps(
                {
                    "event": "audit_dataset_detail_access",
                    "dataset_id": dataset_id,
                    "user_id": get_active_user(),
                    "status": "denied_or_missing",
                }
            )
        )
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' was not found or is not available for ingestion detail.",
        ) from exc


@router.delete("/api/datasets-id/{dataset_id}")
def delete_dataset_by_id(dataset_id: str):
    try:
        result = postgis_store.delete_dataset(dataset_id)
        if int(result.get("datasets_deleted", 0)) <= 0:
            logger.info(
                json.dumps(
                    {
                        "event": "audit_dataset_delete",
                        "dataset_id": dataset_id,
                        "user_id": get_active_user(),
                        "status": "denied_or_missing",
                    }
                )
            )
            raise HTTPException(
                status_code=404,
                detail=f"Dataset '{dataset_id}' was not found or is not available for deletion.",
            )

        logger.info(
            json.dumps(
                {
                    "event": "audit_dataset_delete",
                    "dataset_id": dataset_id,
                    "user_id": get_active_user(),
                    "status": "success",
                    "events_deleted": int(result.get("events_deleted", 0)),
                    "datasets_deleted": int(result.get("datasets_deleted", 0)),
                    "codebook_deleted": int(result.get("codebook_deleted", 0)),
                }
            )
        )
        return {"status": "deleted", **result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete dataset '%s': %s", dataset_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to delete dataset '{dataset_id}'.") from exc

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends

from backend.auth.dependencies import get_current_user
from dynamic_analyst.services.datasets import list_session_datasets
from dynamic_analyst.session_state import get_active_user

router = APIRouter()
logger = logging.getLogger("adk_server")


@router.get("/api/datasets")
def list_datasets_endpoint(current_user: dict = Depends(get_current_user)):
    try:
        rows = list_session_datasets()
        logger.info(
            json.dumps(
                {
                    "event": "audit_dataset_list",
                    "user_id": get_active_user(),
                    "count": len(rows),
                }
            )
        )
        return {"datasets": rows}
    except Exception:
        return {"datasets": []}

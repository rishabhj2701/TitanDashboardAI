from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.services.uploads import upload_dataset, upload_dataset_url
from dynamic_analyst.pipeline_react import clear_schema_cache_for_session
from dynamic_analyst.session_state import set_active_session

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadUrlRequest(BaseModel):
    url: str
    dataset_name: Optional[str] = None
    entity_type: Optional[str] = None


def _require_session(x_session_id: Optional[str]) -> str:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id header")
    set_active_session(x_session_id)
    return x_session_id


@router.post("/api/upload")
async def upload_dataset_endpoint(
    file: UploadFile = File(...),
    dataset_name: Optional[str] = Form(None),
    entity_type: Optional[str] = Form(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    current_user: dict = Depends(get_current_user),
):
    _require_session(x_session_id)
    result = await upload_dataset(
        file=file,
        dataset_name=dataset_name,
        entity_type=entity_type,
        x_session_id=x_session_id,
    )
    try:
        clear_schema_cache_for_session(x_session_id)
    except Exception as exc:
        logger.warning("Schema-cache clear warning after upload: %s", exc)
    return result


@router.post("/api/upload-url")
async def upload_dataset_url_endpoint(
    payload: UploadUrlRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
    current_user: dict = Depends(get_current_user),
):
    _require_session(x_session_id)
    result = await upload_dataset_url(
        url=payload.url,
        dataset_name=payload.dataset_name,
        entity_type=payload.entity_type,
        x_session_id=x_session_id,
    )
    try:
        clear_schema_cache_for_session(x_session_id)
    except Exception as exc:
        logger.warning("Schema-cache clear warning after upload-url: %s", exc)
    return result

from typing import Optional

from fastapi import HTTPException, Header
from jose import JWTError

from .jwt_utils import verify_token
from .db import get_user_by_id


async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    # Bypass authentication - return a default user
    return {
        "user_id": "bypass_user",
        "email": "bypass@example.com",
        "name": "Bypass User",
        "provider": "bypass"
    }


async def get_optional_user(
    authorization: Optional[str] = Header(None),
) -> Optional[dict]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None

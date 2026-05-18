from typing import Optional

from fastapi import HTTPException, Header
from jose import JWTError

from .jwt_utils import verify_token
from .db import get_user_by_id


import os

REQUIRE_USER_ID = os.environ.get("REQUIRE_USER_ID", "1").strip().lower() in {"1", "true", "yes", "on"}

async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    if not REQUIRE_USER_ID:
        # Auth disabled for local deployment — return static dev user
        return {
            "user_id": "dev-user",
            "email": "dev@localhost",
            "name": "Dev User"
        }

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = verify_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing sub claim")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(None),
) -> Optional[dict]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None

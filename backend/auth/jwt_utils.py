from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt, JWTError  # noqa: F401 — re-export JWTError for callers
from dynamic_analyst.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES


def create_access_token(user_id: str, email: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

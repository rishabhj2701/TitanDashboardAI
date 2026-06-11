import logging
import os
import threading
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dynamic_analyst import postgis_store
from dynamic_analyst.config import validate_agent_config
from dynamic_analyst.storage.postgis import db_pool
from backend.routers.analysis import router as analysis_router
from backend.routers.capabilities import router as capabilities_router
from backend.routers.chat import router as chat_router
from backend.routers.cv_query import router as cv_query_router
from backend.routers.cv_roads import router as cv_roads_router
from backend.routers.cv_runs import (
    router as cv_runs_router,
    set_cv_cache_clearer,
)
from backend.routers.dataset_details import router as dataset_details_router
from backend.routers.dataset_mappings import router as dataset_mappings_router
from backend.routers.roads_bbox import (
    clear_roads_bbox_cache,
    router as roads_bbox_router,
)
from backend.routers.roads_tiles import (
    clear_road_tiles_cache,
    router as roads_tiles_router,
)
from backend.routers.session_datasets import router as session_datasets_router
from backend.routers.uploads import router as uploads_router
from backend.routers.data_quality import router as data_quality_router
from dynamic_analyst.session_state import set_active_session, set_active_user, DEFAULT_DEBUG_USER_ID
from backend.routers.auth import router as auth_router
from backend.auth.jwt_utils import verify_token

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("adk_server")
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
PRODUCTION_ENVS = {"production", "prod"}
DEV_INSECURE_JWT_SECRET = "dev-insecure-jwt-secret-change-me"
REQUIRE_USER_ID = os.environ.get("REQUIRE_USER_ID", "1").strip().lower() in {"1", "true", "yes", "on"}
ALLOW_DEBUG_USER_HEADER = os.environ.get("ALLOW_DEBUG_USER_HEADER", "0").strip().lower() in {"1", "true", "yes", "on"}
JWT_SECRET_ENV = os.environ.get("JWT_SECRET", "").strip()
REQUEST_TIMING_LOG_ENABLED = os.environ.get("REQUEST_TIMING_LOG_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_LOG_HEALTH = os.environ.get("REQUEST_TIMING_LOG_HEALTH", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_INCLUDE_QUERY = os.environ.get("REQUEST_TIMING_INCLUDE_QUERY", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_ADD_HEADER = os.environ.get("REQUEST_TIMING_ADD_HEADER", "0").strip().lower() in {"1", "true", "yes", "on"}
REQUEST_TIMING_SLOW_MS = float(os.environ.get("REQUEST_TIMING_SLOW_MS", "1000"))
DB_HEALTH_STMT_TIMEOUT_MS = int(os.environ.get("DB_HEALTH_STMT_TIMEOUT_MS", "1500"))
DB_HEALTH_CACHE_TTL_S = max(0.0, float(os.environ.get("DB_HEALTH_CACHE_TTL_S", "3.0")))
_cors_raw = os.environ.get("CORS_ALLOW_ORIGINS", "*" if APP_ENV not in {"production", "prod"} else "")
CORS_ALLOW_ORIGINS = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
PUBLIC_NO_AUTH_PATHS = (
    "/healthz",
    "/health/db",
    "/api/health/db",
    "/api/health/db/live",
    "/api/auth/",
    "/api/capabilities",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/graphs",
)

def validate_production_config() -> None:
    if APP_ENV not in PRODUCTION_ENVS:
        return
    issues: list[str] = []
    if not JWT_SECRET_ENV:
        issues.append("JWT_SECRET must be configured.")
    elif JWT_SECRET_ENV == DEV_INSECURE_JWT_SECRET:
        issues.append("JWT_SECRET cannot use the insecure development default.")
    if not CORS_ALLOW_ORIGINS or "*" in CORS_ALLOW_ORIGINS:
        issues.append("CORS_ALLOW_ORIGINS must be explicit and cannot include '*'.")
    if not os.environ.get("POSTGIS_DSN", "").strip():
        issues.append("POSTGIS_DSN must be configured.")
    if not REQUIRE_USER_ID:
        issues.append("REQUIRE_USER_ID must be enabled.")
    if issues:
        raise RuntimeError(
            "Invalid production configuration:\n- " + "\n- ".join(issues)
        )


validate_production_config()

app = FastAPI(title="Google ADK Traffic Agent")

GRAPH_DIR = Path("graphs")
GRAPH_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/graphs", StaticFiles(directory=str(GRAPH_DIR)), name="graphs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(auth_router)
app.include_router(capabilities_router)
app.include_router(chat_router)
app.include_router(analysis_router)
app.include_router(cv_query_router)
app.include_router(cv_roads_router)
app.include_router(cv_runs_router)
app.include_router(dataset_details_router)
app.include_router(dataset_mappings_router)
app.include_router(roads_bbox_router)
app.include_router(roads_tiles_router)
app.include_router(session_datasets_router)
app.include_router(uploads_router)
app.include_router(data_quality_router)

_DB_HEALTH_CACHE_LOCK = threading.Lock()
_DB_HEALTH_CACHE: tuple[float, int, dict] | None = None


@app.on_event("startup")
def _startup_cleanup():
    validate_agent_config()

    if os.environ.get("CLEAR_POSTGIS_ON_STARTUP", "0") == "1":
        try:
            cleared = postgis_store.clear_all_data()
            logger.info("PostGIS startup clear: %s", cleared)
        except Exception as exc:
            logger.warning("PostGIS startup clear warning: %s", exc)
    else:
        logger.info("PostGIS startup: keeping existing data (set CLEAR_POSTGIS_ON_STARTUP=1 to clear)")


@app.on_event("shutdown")
def _shutdown_cleanup():
    if os.environ.get("CLEAR_POSTGIS_ON_SHUTDOWN", "0") == "1":
        try:
            cleared = postgis_store.clear_all_data()
            logger.info("PostGIS shutdown clear: %s", cleared)
        except Exception as exc:
            logger.warning("PostGIS shutdown clear warning: %s", exc)
    else:
        logger.info("PostGIS shutdown: keeping data (set CLEAR_POSTGIS_ON_SHUTDOWN=1 to clear)")


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    request_path = request.url.path or ""
    sid = request.headers.get("x-session-id")
    if sid:
        set_active_session(sid)
    # Try JWT auth first
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        try:
            payload = verify_token(token)
            uid = payload.get("sub")
            if uid:
                set_active_user(uid)
            else:
                return JSONResponse(status_code=401, content={"detail": "Token missing sub claim"})
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
    else:
        # Fallback to debug/multi-user header logic
        req_state_user = getattr(request.state, "user_id", None)
        debug_user = request.headers.get("x-debug-user-id")
        use_debug_user = bool(debug_user and ALLOW_DEBUG_USER_HEADER)
        resolved_user = (req_state_user or (debug_user if use_debug_user else "") or "").strip()
        is_public = any(
            request_path == public_path or request_path.startswith(public_path)
            for public_path in PUBLIC_NO_AUTH_PATHS
        )
        if not resolved_user and REQUIRE_USER_ID and APP_ENV in {"production", "prod"} and not is_public:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authenticated user id is required."},
            )
        if not resolved_user and APP_ENV not in {"production", "prod"}:
            resolved_user = DEFAULT_DEBUG_USER_ID
        if resolved_user:
            set_active_user(resolved_user)
    response = await call_next(request)
    return response


def _should_log_request_timing(path: str) -> bool:
    if path.startswith("/api/"):
        return True
    if path in {"/healthz", "/health/db", "/api/health/db/live"}:
        return REQUEST_TIMING_LOG_HEALTH
    return False


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _build_db_health_payload() -> tuple[dict, int]:
    db = db_pool.ping_db(DB_HEALTH_STMT_TIMEOUT_MS)
    pool = db_pool.get_pool_stats()
    pressure = "high" if pool["utilization"] >= 0.9 else ("medium" if pool["utilization"] >= 0.7 else "low")
    payload = {
        "status": "ok" if db["ok"] else "degraded",
        "db": db,
        "pool": {
            **pool,
            "pressure": pressure,
        },
    }
    status_code = 200 if db["ok"] else 503
    return payload, status_code


def _db_health(force_refresh: bool = False) -> tuple[dict, int]:
    global _DB_HEALTH_CACHE
    now = time.perf_counter()
    if not force_refresh and DB_HEALTH_CACHE_TTL_S > 0:
        with _DB_HEALTH_CACHE_LOCK:
            cached = _DB_HEALTH_CACHE
        if cached and (now - cached[0]) <= DB_HEALTH_CACHE_TTL_S:
            payload = dict(cached[2])
            payload["cached"] = True
            payload["cache_age_ms"] = round((now - cached[0]) * 1000.0, 1)
            payload["cache_ttl_s"] = DB_HEALTH_CACHE_TTL_S
            return payload, cached[1]

    payload, status_code = _build_db_health_payload()
    payload["cached"] = False
    payload["cache_age_ms"] = 0.0
    payload["cache_ttl_s"] = DB_HEALTH_CACHE_TTL_S
    with _DB_HEALTH_CACHE_LOCK:
        _DB_HEALTH_CACHE = (now, status_code, dict(payload))
    return payload, status_code


@app.get("/health/db")
def health_db():
    payload, status_code = _db_health(force_refresh=False)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/health/db")
def api_health_db():
    payload, status_code = _db_health(force_refresh=False)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/health/db/live")
def api_health_db_live():
    payload, status_code = _db_health(force_refresh=True)
    return JSONResponse(status_code=status_code, content=payload)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    path = request.url.path or "/"
    if not REQUEST_TIMING_LOG_ENABLED or not _should_log_request_timing(path):
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    response = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if response is not None and REQUEST_TIMING_ADD_HEADER:
            response.headers["X-Request-Duration-Ms"] = f"{elapsed_ms:.1f}"
        uri = path
        query = request.url.query
        if REQUEST_TIMING_INCLUDE_QUERY and query:
            uri = f"{path}?{query}"
        client_ip = request.client.host if request.client else "-"
        slow_threshold = REQUEST_TIMING_SLOW_MS if not path.startswith("/api/chat") else 60_000
        log_level = logging.WARNING if status_code >= 500 or elapsed_ms >= slow_threshold else logging.INFO
        logger.log(
            log_level,
            "http.request method=%s path=%s status=%s duration_ms=%.1f client=%s",
            request.method,
            uri,
            status_code,
            elapsed_ms,
            client_ip,
        )


def _clear_cv_map_caches() -> None:
    clear_roads_bbox_cache()
    clear_road_tiles_cache()


set_cv_cache_clearer(_clear_cv_map_caches)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

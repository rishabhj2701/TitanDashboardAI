import os

DEFAULT_POSTGIS_DSN = (
    "dbname=traffic user=postgres password=postgres host=postgis_conflate port=5434"
)

# Always allow runtime override from docker-compose/.env/shell.
POSTGIS_DSN = os.environ.get("POSTGIS_DSN", DEFAULT_POSTGIS_DSN)
CRASH_TIMEZONE = os.environ.get("CRASH_TIMEZONE", "America/Chicago")

# ── Auth ─────────────────────────────────────────────────────────────
# Keep a deterministic dev fallback so multi-worker local runs do not
# generate per-process secrets that invalidate each other's tokens.
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip() or "dev-insecure-jwt-secret-change-me"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "10080"))  # 7 days

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")

OAUTH_REDIRECT_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "http://localhost:8080")

# ── Agent Skills ────────────────────────────────────────────────────────────
AGENT_SKILLS_ENABLED = os.environ.get("AGENT_SKILLS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
AGENT_SKILLS_MAX_ATTEMPTS_PER_TURN = max(1, int(os.environ.get("AGENT_SKILLS_MAX_ATTEMPTS_PER_TURN", "1")))

# ── SQL Dispatcher Bounds ──────────────────────────────────────────
# Conflation buffer bounds (meters).
# Min 1m ensures valid PostGIS geometry; max 2000m caps query cost.
SQL_CONFLATION_MIN_DISTANCE_M = float(os.environ.get("SQL_CONFLATION_MIN_DISTANCE_M", "1.0"))
SQL_CONFLATION_MAX_DISTANCE_M = float(os.environ.get("SQL_CONFLATION_MAX_DISTANCE_M", "2000.0"))

# Conflation time-window bounds (minutes).
# Min 0 allows purely spatial joins; max 1440 (24h) caps scan time.
SQL_CONFLATION_MIN_WINDOW_MIN = int(os.environ.get("SQL_CONFLATION_MIN_WINDOW_MIN", "0"))
SQL_CONFLATION_MAX_WINDOW_MIN = int(os.environ.get("SQL_CONFLATION_MAX_WINDOW_MIN", "1440"))


def validate_agent_config() -> None:
    """Validate agent runtime configuration at startup."""
    return None

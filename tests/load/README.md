# k6 Load Tests

## Backend Health + Area Analyze

Run a DB/pool pressure load on `/health/db`:

```bash
k6 run tests/load/k6_backend.js \
  -e BASE_URL=http://localhost:8080 \
  -e HEALTH_RPS=30 \
  -e HEALTH_DURATION=3m
```

Include concurrent area analysis traffic:

```bash
k6 run tests/load/k6_backend.js \
  -e BASE_URL=http://localhost:8080 \
  -e ENABLE_AREA_ANALYZE=1 \
  -e AREA_RPS=2 \
  -e AREA_DURATION=3m \
  -e CV_DATASET_ID=<cv_dataset_id> \
  -e CRASH_DATASET_ID=<crash_dataset_id> \
  -e WORKZONE_DATASET_ID=<workzone_dataset_id>

# If you hit backend directly (no nginx proxy), use:
# -e BASE_URL=http://localhost:8000 -e HEALTH_PATH=/health/db

# To bypass cache for diagnostics, hit the live endpoint:
# -e HEALTH_PATH=/api/health/db/live
```

Optional auth header:

```bash
-e AUTH_BEARER=<jwt_token>
```

Optional strict mode (area analyze must return `200`):

```bash
-e STRICT_ANALYZE_200=1
```

## Mixed 10-User Flow (Recommended)

Lightweight mixed traffic across capabilities, roads bbox, roads tiles, and optional chat/area.

```bash
k6 run tests/load/k6_user_mix.js \
  -e BASE_URL=http://localhost:8080 \
  -e USERS=10 \
  -e DURATION=3m
```

Optional toggles:

```bash
-e ENABLE_CHAT=1 \
-e ENABLE_AREA_ANALYZE=1 \
-e CV_DATASET_ID=<cv_dataset_id> \
-e CRASH_DATASET_ID=<crash_dataset_id>
```

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = String(__ENV.BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
const HEALTH_PATH = String(__ENV.HEALTH_PATH || "/api/health/db");
const SESSION_ID = String(__ENV.SESSION_ID || "k6-load-session");
const AUTH_BEARER = String(__ENV.AUTH_BEARER || "").trim();
const ENABLE_AREA_ANALYZE = _asBool(__ENV.ENABLE_AREA_ANALYZE, false);
const STRICT_ANALYZE_200 = _asBool(__ENV.STRICT_ANALYZE_200, false);
const HEALTH_DURATION = String(__ENV.HEALTH_DURATION || "2m");
const HEALTH_RPS = _asInt(__ENV.HEALTH_RPS, 20);
const AREA_DURATION = String(__ENV.AREA_DURATION || "2m");
const AREA_RPS = _asInt(__ENV.AREA_RPS, 2);
const PREALLOCATED_VUS = _asInt(__ENV.PREALLOCATED_VUS, 20);
const MAX_VUS = _asInt(__ENV.MAX_VUS, 120);
const AREA_ANALYZE_MODE = String(__ENV.AREA_ANALYZE_MODE || "aggregate").trim().toLowerCase();

const failedHealthChecks = new Counter("failed_health_db_checks");
const failedAreaChecks = new Counter("failed_area_analyze_checks");

function _asBool(value, defaultValue) {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return defaultValue;
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

function _asInt(value, defaultValue) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function _headers(jsonBody = false) {
  const headers = { "X-Session-Id": SESSION_ID };
  if (AUTH_BEARER) headers.Authorization = `Bearer ${AUTH_BEARER}`;
  if (jsonBody) headers["Content-Type"] = "application/json";
  return headers;
}

function _safeJson(response) {
  try {
    return response.json();
  } catch (_) {
    return null;
  }
}

function _areaPolygon() {
  const raw = String(__ENV.AREA_POLYGON_JSON || "").trim();
  if (raw) {
    try {
      return JSON.parse(raw);
    } catch (_) {
      // Fall through to default polygon.
    }
  }
  return {
    type: "Polygon",
    coordinates: [
      [
        [-90.246, 38.561],
        [-90.118, 38.561],
        [-90.118, 38.688],
        [-90.246, 38.688],
        [-90.246, 38.561],
      ],
    ],
  };
}

const AREA_POLYGON = _areaPolygon();
const AREA_ANALYZE_PAYLOAD = {
  polygon: AREA_POLYGON,
  analysis_mode: AREA_ANALYZE_MODE,
  include_unmatched: false,
};

if (__ENV.CV_DATASET_ID) AREA_ANALYZE_PAYLOAD.cv_dataset_id = String(__ENV.CV_DATASET_ID);
if (__ENV.CRASH_DATASET_ID) AREA_ANALYZE_PAYLOAD.crash_dataset_id = String(__ENV.CRASH_DATASET_ID);
if (__ENV.WORKZONE_DATASET_ID) AREA_ANALYZE_PAYLOAD.workzone_dataset_id = String(__ENV.WORKZONE_DATASET_ID);

const scenarios = {
  health_db: {
    executor: "constant-arrival-rate",
    rate: HEALTH_RPS,
    timeUnit: "1s",
    duration: HEALTH_DURATION,
    preAllocatedVUs: PREALLOCATED_VUS,
    maxVUs: MAX_VUS,
    exec: "healthDb",
  },
};

if (ENABLE_AREA_ANALYZE) {
  scenarios.area_analyze = {
    executor: "constant-arrival-rate",
    rate: AREA_RPS,
    timeUnit: "1s",
    duration: AREA_DURATION,
    preAllocatedVUs: Math.max(5, Math.floor(PREALLOCATED_VUS / 2)),
    maxVUs: MAX_VUS,
    exec: "areaAnalyze",
  };
}

export const options = {
  scenarios,
  thresholds: {
    checks: ["rate>0.98"],
    http_req_failed: ["rate<0.02"],
    "http_req_duration{route:health_db}": ["p(95)<1200"],
    "http_req_duration{route:area_analyze}": ["p(95)<10000"],
  },
};

export function healthDb() {
  const response = http.get(`${BASE_URL}${HEALTH_PATH}`, {
    headers: _headers(false),
    tags: { route: "health_db" },
  });
  const payload = _safeJson(response);
  const ok = check(response, {
    "health/db status=200": (r) => r.status === 200,
    "health/db has db.ok": () => payload && payload.db && typeof payload.db.ok === "boolean",
    "health/db has pool stats": () => payload && payload.pool && typeof payload.pool.in_use === "number",
  });
  if (!ok) failedHealthChecks.add(1);
  sleep(0.1);
}

export function areaAnalyze() {
  const response = http.post(`${BASE_URL}/api/area/analyze`, JSON.stringify(AREA_ANALYZE_PAYLOAD), {
    headers: _headers(true),
    tags: { route: "area_analyze" },
  });
  const payload = _safeJson(response);
  const allowedStatuses = STRICT_ANALYZE_200 ? new Set([200]) : new Set([200, 400, 404, 422]);
  const ok = check(response, {
    "area/analyze status allowed": (r) => allowedStatuses.has(r.status),
    "area/analyze json body": () => payload !== null,
  });
  if (!ok) failedAreaChecks.add(1);
  sleep(0.2);
}

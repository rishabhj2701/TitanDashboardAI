import http from "k6/http";
import { check, sleep } from "k6";
import exec from "k6/execution";

const BASE_URL = String(__ENV.BASE_URL || "http://localhost:8080").replace(/\/+$/, "");
const AUTH_BEARER = String(__ENV.AUTH_BEARER || "").trim();
const SESSION_PREFIX = String(__ENV.SESSION_PREFIX || "k6-user");
const USERS = _asInt(__ENV.USERS, 10);
const DURATION = String(__ENV.DURATION || "3m");
const ENABLE_CHAT = _asBool(__ENV.ENABLE_CHAT, true);
const ENABLE_AREA_ANALYZE = _asBool(__ENV.ENABLE_AREA_ANALYZE, false);
const CHAT_WEIGHT = _asFloat(__ENV.CHAT_WEIGHT, 0.2);
const AREA_WEIGHT = _asFloat(__ENV.AREA_WEIGHT, 0.1);

const TILE_COORDS = [
  [7, 19, 48],
  [8, 38, 96],
  [9, 75, 192],
  [10, 150, 384],
  [11, 300, 768],
];

const AREA_ANALYZE_PAYLOAD = {
  polygon: {
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
  },
  analysis_mode: String(__ENV.AREA_ANALYZE_MODE || "aggregate").trim().toLowerCase(),
  include_unmatched: false,
};

if (__ENV.CV_DATASET_ID) AREA_ANALYZE_PAYLOAD.cv_dataset_id = String(__ENV.CV_DATASET_ID);
if (__ENV.CRASH_DATASET_ID) AREA_ANALYZE_PAYLOAD.crash_dataset_id = String(__ENV.CRASH_DATASET_ID);
if (__ENV.WORKZONE_DATASET_ID) AREA_ANALYZE_PAYLOAD.workzone_dataset_id = String(__ENV.WORKZONE_DATASET_ID);

export const options = {
  scenarios: {
    user_mix: {
      executor: "constant-vus",
      vus: USERS,
      duration: DURATION,
      gracefulStop: "30s",
    },
  },
  thresholds: {
    checks: ["rate>0.90"],
    http_req_failed: ["rate<0.10"],
  },
};

function _asBool(value, defaultValue) {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return defaultValue;
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

function _asInt(value, defaultValue) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function _asFloat(value, defaultValue) {
  const parsed = Number.parseFloat(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function _headers(jsonBody = false) {
  const sessionId = `${SESSION_PREFIX}-${exec.vu.idInTest}`;
  const headers = { "X-Session-Id": sessionId };
  if (AUTH_BEARER) headers.Authorization = `Bearer ${AUTH_BEARER}`;
  if (jsonBody) headers["Content-Type"] = "application/json";
  return headers;
}

function _pickTile() {
  const idx = Math.floor(Math.random() * TILE_COORDS.length);
  return TILE_COORDS[idx];
}

export default function () {
  const headers = _headers(false);
  const sessionId = headers["X-Session-Id"];

  const cap = http.get(`${BASE_URL}/api/capabilities`, { headers, tags: { route: "capabilities" } });
  check(cap, {
    "capabilities status=200": (r) => r.status === 200,
  });

  const bbox = http.get(`${BASE_URL}/api/roads/bbox`, { headers, tags: { route: "roads_bbox" } });
  check(bbox, {
    "roads/bbox status ok": (r) => [200, 304].includes(r.status),
  });

  const [z, x, y] = _pickTile();
  const tile = http.get(`${BASE_URL}/tiles/roads/${z}/${x}/${y}.mvt`, { headers, tags: { route: "roads_tile" } });
  check(tile, {
    "roads tile status ok": (r) => [200, 204, 304, 404].includes(r.status),
  });

  const r = Math.random();
  if (ENABLE_CHAT && r < CHAT_WEIGHT) {
    const chatPayload = {
      sessionId,
      message: "Give me a very short status of available traffic data.",
    };
    const chat = http.post(`${BASE_URL}/api/chat`, JSON.stringify(chatPayload), {
      headers: _headers(true),
      tags: { route: "chat" },
      timeout: "60s",
    });
    check(chat, {
      "chat status ok": (resp) => [200, 401, 403].includes(resp.status),
    });
  } else if (ENABLE_AREA_ANALYZE && r < CHAT_WEIGHT + AREA_WEIGHT) {
    const area = http.post(`${BASE_URL}/api/area/analyze`, JSON.stringify(AREA_ANALYZE_PAYLOAD), {
      headers: _headers(true),
      tags: { route: "area_analyze" },
      timeout: "60s",
    });
    check(area, {
      "area analyze status ok": (resp) => [200, 400, 404, 422].includes(resp.status),
    });
  }

  sleep(0.3);
}

const trimTrailingSlash = (value?: string) => (value || '').replace(/\/+$/, '');
const envFlag = (value: unknown, fallback = false): boolean => {
  if (typeof value !== 'string') return fallback;
  const normalized = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  return fallback;
};
const BROWSER_ORIGIN =
  typeof window !== 'undefined' && window.location?.origin
    ? trimTrailingSlash(window.location.origin)
    : '';

export const API_BASE_URL = trimTrailingSlash(import.meta.env.VITE_API_BASE_URL);
export const TILE_API_BASE_URL = trimTrailingSlash(
  import.meta.env.VITE_TILE_API_BASE_URL || API_BASE_URL
);

export const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN || '';


export const ROAD_TILE_URL =
  import.meta.env.VITE_ROAD_TILES_URL ||
  `${TILE_API_BASE_URL || BROWSER_ORIGIN}/tiles/roads/{z}/{x}/{y}.mvt?v=6`;
export const ROAD_TILE_DATASET = import.meta.env.VITE_ROAD_TILES_DATASET || '';
export const USE_ROAD_TILE_MODE = import.meta.env.VITE_USE_ROAD_TILES === '1';

// Optional/experimental modules are opt-in so backend endpoint churn does not
// break core map/chat flows. Defaults are disabled; enable explicitly via env
// when those backend endpoints are available.
export const ENABLE_WEBSITE_BUILDER_ROUTES = envFlag(
  import.meta.env.VITE_ENABLE_WEBSITE_BUILDER_ROUTES,
  false
);
export const ENABLE_CHART_EDITING = envFlag(import.meta.env.VITE_ENABLE_CHART_EDITING, false);

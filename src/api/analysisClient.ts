import { apiFetchApp } from './http';

export interface CrashAnalyzeRequestPayload {
  crash_lat: number;
  crash_lon: number;
  crash_ts?: string | null;
  accident_date?: string | null;
  accident_time?: string | null;
  severity?: string | null;
  road_segment_id?: string | null;
  crash_id?: string | null;
  dataset_id?: string | null;
  cv_dataset_id?: string | null;
  distance_m?: number;
  window_minutes?: number;
}

export interface WorkzoneAnalyzeRequestPayload {
  workzone_id: string;
  road_segment_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  dataset_id?: string | null;
  cv_dataset_id?: string | null;
  distance_m?: number;
}

export interface AreaAnalyzeRequestPayload {
  polygon: GeoJSON.Polygon | Record<string, unknown>;
  cv_dataset_id?: string | null;
  crash_dataset_id?: string | null;
  workzone_dataset_id?: string | null;
  include_unmatched?: boolean;
  analysis_mode?: 'auto' | 'detail' | 'aggregate';
  max_map_points?: number;
  max_hard_brake_points?: number;
  max_roads?: number;
  min_road_points?: number;
}

export interface AnalysisResponsePayload {
  status?: string;
  mode?: 'detail' | 'aggregate';
  response?: string;
  summary?: Record<string, unknown>;
  mapSelection?: {
    points?: unknown[];
    lines?: unknown[];
    overlay?: boolean;
    roadAggregateFilter?: Record<string, unknown>;
  };
  areaAggregate?: {
    label?: string;
    count?: number;
    geojson?: unknown;
    render?: {
      layer_mode?: 'road-network' | 'focus-selection' | 'mixed';
      show_points?: boolean;
    };
    stats?: Record<string, unknown>;
  };
  chartPayload?: unknown[];
}

export const analyzeCrash = async (
  payload: CrashAnalyzeRequestPayload
): Promise<AnalysisResponsePayload> => {
  const response = await apiFetchApp('/api/crash/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const analyzeWorkzone = async (
  payload: WorkzoneAnalyzeRequestPayload
): Promise<AnalysisResponsePayload> => {
  const controller = new AbortController();
  const timeoutMs = 90_000;
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await apiFetchApp('/api/workzone/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    return response.json();
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Workzone analysis timed out. Please try again with a narrower scope.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
};

export const analyzeArea = async (
  payload: AreaAnalyzeRequestPayload,
  options?: { timeoutMs?: number }
): Promise<AnalysisResponsePayload> => {
  const controller = new AbortController();
  const timeoutMs = Math.max(5_000, Number(options?.timeoutMs ?? 300_000));
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await apiFetchApp('/api/area/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    return response.json();
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Area analysis timed out. Try a smaller area or aggregate mode.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
};

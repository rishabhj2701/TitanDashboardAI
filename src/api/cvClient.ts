import { apiFetchApp } from './http';

export interface CvQueryRequestPayload {
  dataset_id?: string;
  road_name?: string;
  limit?: number;
  min_speed?: number;
  max_speed?: number;
  source_table?: string;
}

export interface CvRoadAggregateRequestPayload {
  dataset_id?: string;
  source_table?: string;
  min_points?: number;
  road_name?: string;
  road_names?: string[];
  road_segment_id?: string;
  limit?: number;
}

export interface CvQueryResponsePayload {
  status?: string;
  count?: number;
  data?: unknown[];
  filters_applied?: Record<string, unknown>;
}

export interface CvRoadAggregateResponsePayload {
  status?: string;
  dataset_id?: string;
  count?: number;
  geojson?: unknown;
}

export interface CvRoadsListResponsePayload {
  status?: string;
  count?: number;
  roads?: string[];
}

export const queryCv = async (
  payload: CvQueryRequestPayload
): Promise<CvQueryResponsePayload> => {
  const response = await apiFetchApp('/api/cv/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const aggregateCvRoads = async (
  payload: CvRoadAggregateRequestPayload
): Promise<CvRoadAggregateResponsePayload> => {
  const response = await apiFetchApp('/api/cv/aggregate-roads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const listCvRoads = async (): Promise<CvRoadsListResponsePayload> => {
  const response = await apiFetchApp('/api/cv/roads');
  return response.json();
};

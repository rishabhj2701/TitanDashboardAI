import { apiFetchTile } from './http';

export interface RoadsBbox {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
}

export interface RoadsBboxResponse {
  bbox: RoadsBbox | null;
}

export const getRoadsBbox = async (datasetId?: string): Promise<RoadsBboxResponse> => {
  const params = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : '';
  const response = await apiFetchTile(`/api/roads/bbox${params}`);
  return response.json();
};

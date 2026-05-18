import type { GeneratedChartPayload } from '../../types/charts';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  charts?: GeneratedChartPayload[];
}

export interface ExternalChatMessage extends ChatMessage {
  id: string;
}

export interface UploadedFileData {
  fileName: string;
  fileType: string;
  rowCount: number;
  columns: string[];
  data: unknown[];
  preview: string;
  datasetType?: string;
  datasetName?: string;
  datasetId?: string;
  queryableFields?: Array<{
    queryName: string;
    sourceColumn: string;
    enabled: boolean;
    locked?: boolean;
  }>;
}

export interface WorkzoneLinePayload {
  id: string;
  coordinates: [number, number][];
  roadName: string;
  roadSegmentId?: string;
  datasetId?: string;
  status?: string;
  description?: string;
  startDate?: string;
  endDate?: string;
  exclusive?: boolean;
}

export interface CrashAnalyzeDetail {
  crash_lat: number;
  crash_lon: number;
  crash_ts?: string | null;
  accident_date?: string | null;
  accident_time?: string | null;
  severity?: string | null;
  road_segment_id?: string | null;
  crash_id?: string | null;
  distance_m?: number;
  window_minutes?: number;
  cv_dataset_id?: string | null;
}

export interface WorkzoneAnalyzeDetail {
  workzone_id: string;
  road_segment_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  dataset_id?: string | null;
  cv_dataset_id?: string | null;
  distance_m?: number;
}

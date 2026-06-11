import { apiFetchApp } from './http';

export interface CvRunSummary {
  run_id: string;
  schema_name: string;
  created_at?: string;
  ts_start?: string;
  ts_end?: string;
  is_active?: boolean;
  display_name?: string;
  point_count?: number;
  state_code?: string;
  states?: string[];
  region?: string;
  season?: string;
  season_tag?: string;
  tags?: string[];
  is_visible?: boolean;
  is_selectable?: boolean;
}

export interface CvRunsListResponsePayload {
  status?: string;
  active_run_id?: string | null;
  runs?: CvRunSummary[];
}

export interface CvActiveRunResponsePayload {
  status?: string;
  active_run?: {
    run_id: string;
    schema_name: string;
    created_at?: string;
  } | null;
  execution_mode?: 'schema_qualified' | string;
  warnings?: string[];
  search_path_updated?: boolean;
}

export const getCvRuns = async (): Promise<CvRunsListResponsePayload> => {
  const response = await apiFetchApp('/api/cv/runs');
  return response.json();
};

export const getActiveCvRun = async (): Promise<CvActiveRunResponsePayload> => {
  const response = await apiFetchApp('/api/cv/active-run');
  return response.json();
};

export const setActiveCvRun = async (runId: string): Promise<CvActiveRunResponsePayload> => {
  const response = await apiFetchApp('/api/cv/active-run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  });
  return response.json();
};

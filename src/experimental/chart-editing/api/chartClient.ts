import { apiFetchApp } from '../../../api/http';
import { ENABLE_CHART_EDITING } from '../../../config';

export interface FigureEditPayload {
  chartId: string;
  chartTitle?: string;
  instructions: string;
  chartPayload: unknown;
  meta?: unknown;
  history?: unknown[];
}

export interface ApplyPipelinePayload {
  chartId: string;
  chartTitle?: string;
  chartPayload: unknown;
  meta?: unknown;
  appliedParams?: unknown[];
  appliedFilters?: unknown[];
  replaceFilters?: boolean;
}

const PIPELINE_ENDPOINTS: Record<string, string> = {
  hard_braking: '/api/pipelines/hard_braking/apply',
  workzone_analysis: '/api/pipelines/workzone_analysis/apply',
  crash_analysis: '/api/pipelines/crash_analysis/apply',
};

const ensureChartEditingEnabled = () => {
  if (!ENABLE_CHART_EDITING) {
    throw new Error(
      'Chart editing is disabled (set VITE_ENABLE_CHART_EDITING=1 to enable when backend supports it).'
    );
  }
};

export const editFigure = async (payload: FigureEditPayload): Promise<Record<string, unknown>> => {
  ensureChartEditingEnabled();
  const response = await apiFetchApp('/api/figure-edit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const applyPipeline = async (
  pipelineId: string,
  payload: ApplyPipelinePayload
): Promise<Record<string, unknown>> => {
  ensureChartEditingEnabled();
  const endpoint = PIPELINE_ENDPOINTS[pipelineId];
  if (!endpoint) {
    throw new Error(`Unsupported pipeline: ${pipelineId}`);
  }

  const response = await apiFetchApp(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

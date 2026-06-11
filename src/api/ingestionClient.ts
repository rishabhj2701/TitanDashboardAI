import { apiFetchApp } from './http';

export interface DatasetSummary {
  dataset_id?: string;
  name: string;
  entity_type?: string;
  status?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface DatasetListResponse {
  datasets?: DatasetSummary[];
}

export type DatasetDetailResponse = Record<string, unknown>;

export interface UploadUrlPayload {
  url: string;
  dataset_name?: string;
  entity_type?: string;
}

export interface UploadFilePayload {
  file: File;
  dataset_name?: string;
  entity_type?: string;
}

export interface MappingUpdatePayload {
  entity_type?: string | null;
  fields?: Record<string, string | null>;
}

export interface CodeMappingUpdatePayload {
  mappings?: Record<string, string | null>;
}

export interface QueryableFieldPayload {
  query_name?: string | null;
  source_column?: string | null;
  enabled?: boolean;
}

export interface QueryableFieldsUpdatePayload {
  fields?: QueryableFieldPayload[];
}

export interface DeleteDatasetResponse {
  status: string;
  dataset_id: string;
  events_deleted: number;
  datasets_deleted: number;
  codebook_deleted?: number;
}

export const listDatasets = async (): Promise<DatasetListResponse> => {
  const response = await apiFetchApp('/api/datasets');
  return response.json();
};

export const getDatasetById = async (datasetId: string): Promise<DatasetDetailResponse> => {
  const response = await apiFetchApp(`/api/datasets-id/${encodeURIComponent(datasetId)}`);
  return response.json();
};

export const deleteDatasetById = async (datasetId: string): Promise<DeleteDatasetResponse> => {
  const response = await apiFetchApp(`/api/datasets-id/${encodeURIComponent(datasetId)}`, {
    method: 'DELETE',
  });
  return response.json();
};

export const uploadDatasetFile = async (
  fileOrPayload: File | UploadFilePayload
): Promise<Record<string, unknown>> => {
  const payload: UploadFilePayload =
    fileOrPayload instanceof File
      ? { file: fileOrPayload }
      : fileOrPayload;

  const formData = new FormData();
  formData.append('file', payload.file);
  if (payload.dataset_name) {
    formData.append('dataset_name', payload.dataset_name);
  }
  if (payload.entity_type) {
    formData.append('entity_type', payload.entity_type);
  }
  const response = await apiFetchApp('/api/upload', {
    method: 'POST',
    body: formData,
  });
  return response.json();
};

export const uploadDatasetUrl = async (
  payload: UploadUrlPayload
): Promise<Record<string, unknown>> => {
  const response = await apiFetchApp('/api/upload-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const updateDatasetMapping = async (
  datasetName: string,
  payload: MappingUpdatePayload
): Promise<DatasetDetailResponse> => {
  const response = await apiFetchApp(`/api/datasets/${encodeURIComponent(datasetName)}/mapping`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const updateDatasetCodeMappings = async (
  datasetName: string,
  payload: CodeMappingUpdatePayload
): Promise<DatasetDetailResponse> => {
  const response = await apiFetchApp(`/api/datasets/${encodeURIComponent(datasetName)}/code-mappings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

export const updateDatasetQueryableFields = async (
  datasetName: string,
  payload: QueryableFieldsUpdatePayload
): Promise<DatasetDetailResponse> => {
  const response = await apiFetchApp(`/api/datasets/${encodeURIComponent(datasetName)}/queryable-fields`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

import { useEffect, useState } from 'react';
import './IngestionInspector.css';
import {
  deleteDatasetById,
  getDatasetById,
  listDatasets,
  updateDatasetCodeMappings,
  updateDatasetMapping,
  updateDatasetQueryableFields,
  uploadDatasetFile,
  uploadDatasetUrl,
} from '../api/ingestionClient';
import { formatDateRangeHuman, formatDateTimeHuman } from '../utils/dateTime';

type GeoInfo = {
  lat_column?: string | null;
  lon_column?: string | null;
  geometry_column?: string | null;
};

type RoadMatchInfo = {
  enabled?: boolean;
  mode?: string;
  match_rate?: number;
  matched?: number;
  unmatched?: number;
  max_distance_m?: number;
  road_column?: string | null;
  road_segment_id_column?: string | null;
  lat_column?: string | null;
  lon_column?: string | null;
  geometry_column?: string | null;
};

type CodebookInfo = {
  available?: boolean;
  attributes?: number;
  matches?: string[];
  candidates?: string[];
  missing?: string[];
  mappings?: Record<string, CodeMappingMeta | string>;
  updated_at?: string;
};

type CodeMappingMeta = {
  attr?: string;
  method?: string;
  confidence?: number;
  unique_values?: number;
  mapped_unique_values?: number;
  coverage?: number;
  sample_labels?: Array<{ code: string; label: string }>;
  sample_values?: string[];
};

type DatasetSummary = {
  dataset_id?: string;
  name: string;
  entity_type?: string;
  status?: string;
  created_at?: string;
  stats?: Record<string, any>;
  row_count?: number;
  columns?: string[];
  ingested_at?: string;
  geo?: GeoInfo;
  road_match?: RoadMatchInfo;
  codebook?: CodebookInfo;
  source?: string;
  run_id?: string;
  schema_name?: string;
  ts_start?: string;
  ts_end?: string;
  is_active?: boolean;
};

type ColumnStat = {
  name: string;
  dtype: string;
  nulls: number;
  unique: number;
  example?: string | number | null;
};

type MappingInfo = {
  entity_type?: string | null;
  fields?: Record<string, string | null>;
  updated_at?: string;
  auto?: boolean;
};

type MappingStatus = {
  entity_type?: string;
  entity_label?: string;
  required_fields?: string[];
  optional_fields?: string[];
  missing_required?: string[];
  mapped_required?: string[];
  mapped_optional?: string[];
  mapped_total?: number;
  expected_total?: number;
  completion_ratio?: number;
  level?: 'ready' | 'partial' | 'missing_required' | 'unmapped' | string;
  message?: string;
  warnings?: string[];
};

type EntitySchema = {
  entity_type?: string;
  label?: string;
  required_fields?: string[];
  optional_fields?: string[];
  description?: string;
};

type MappingStats = {
  missing_source_columns?: Record<string, string>;
  unmapped_fields?: string[];
  coverage?: Record<string, number>;
  derived_timestamp?: {
    date_column?: string;
    time_column?: string;
    method?: string;
  };
};

type ConnectionInfo = {
  canonical?: {
    name: string;
    row_count?: number;
    mapping_stats?: MappingStats;
  } | null;
  mapped_from?: string | null;
  derived_datasets?: string[];
  conflations?: { dataset: string; role: string }[];
};

type DatasetDetail = DatasetSummary & {
  dataset?: string;
  column_stats?: ColumnStat[];
  preview_rows?: Record<string, any>[];
  stats_sample_size?: number;
  mapping?: MappingInfo;
  mapping_status?: MappingStatus;
  mapped_preview?: Record<string, string | number | null>[];
  connections?: ConnectionInfo;
  auto_mapping_error?: string | null;
  codebook_attributes?: string[];
  queryable_fields?: {
    entity_type?: string;
    fields?: Array<{
      query_name?: string;
      source_column?: string;
      enabled?: boolean;
      locked?: boolean;
    }>;
    updated_at?: string;
  };
  entity_schemas?: Record<string, EntitySchema>;
  warnings?: string[];
};

type Props = {
  onClose: () => void;
};

const formatPercent = (value?: number) => {
  if (value === undefined || value === null || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
};

const formatPreviewValue = (value: any) => {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  try {
    const text = JSON.stringify(value);
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
  } catch {
    return String(value);
  }
};

const MAPPING_FIELDS = [
  { key: 'primary_id', label: 'Primary ID' },
  { key: 'timestamp', label: 'Timestamp (Combined)' },
  { key: 'event_date', label: 'Event Date' },
  { key: 'event_time', label: 'Event Time' },
  { key: 'start_time', label: 'Start Time (Workzones)' },
  { key: 'end_time', label: 'End Time (Workzones)' },
  { key: 'latitude', label: 'Latitude' },
  { key: 'longitude', label: 'Longitude' },
  { key: 'geometry', label: 'Geometry' },
  { key: 'road_name', label: 'Road Name' },
  { key: 'road_id', label: 'Road ID (Optional)' },
];

const FALLBACK_ENTITY_SCHEMAS: Record<string, EntitySchema> = {
  crash: {
    entity_type: 'crash',
    label: 'Crash',
    required_fields: ['event_date', 'event_time', 'latitude', 'longitude'],
    optional_fields: ['primary_id', 'timestamp', 'geometry', 'road_name', 'road_id'],
  },
  workzone: {
    entity_type: 'workzone',
    label: 'Work Zone',
    required_fields: ['start_time', 'end_time', 'road_name'],
    optional_fields: ['primary_id', 'timestamp', 'latitude', 'longitude', 'geometry', 'road_id'],
  },
  event: {
    entity_type: 'event',
    label: 'Generic Event',
    required_fields: ['timestamp', 'latitude', 'longitude'],
    optional_fields: ['primary_id', 'event_date', 'event_time', 'geometry', 'road_name', 'road_id'],
  },
};

const UPLOAD_ENTITY_OPTIONS = [
  { value: 'crash', label: 'Crash' },
  { value: 'workzone', label: 'Work Zone' },
  { value: 'event', label: 'Generic Event' },
];

const getEntitySchemas = (detail: DatasetDetail | null) => {
  const fromApi = detail?.entity_schemas;
  return fromApi && Object.keys(fromApi).length ? fromApi : FALLBACK_ENTITY_SCHEMAS;
};

const getFieldMeta = (fieldKey: string) => {
  return MAPPING_FIELDS.find((field) => field.key === fieldKey) ?? { key: fieldKey, label: fieldKey };
};

const tokenizeColumn = (name: string) =>
  name
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .split(/[^a-zA-Z0-9]+/)
    .map((token) => token.toLowerCase())
    .filter(Boolean);

const findColumn = (columns: string[], keywords: string[]) => {
  const lowered = columns.map((col) => col.toLowerCase());
  const tokenized = new Map(columns.map((col) => [col, tokenizeColumn(col)]));

  for (const keyword of keywords) {
    const target = keyword.toLowerCase();
    const idx = lowered.indexOf(target);
    if (idx >= 0) {
      return columns[idx];
    }
  }

  for (const keyword of keywords) {
    const parts = keyword
      .toLowerCase()
      .split(/[^a-zA-Z0-9]+/)
      .filter(Boolean);
    if (parts.length === 0) continue;
    for (const col of columns) {
      const tokens = tokenized.get(col) ?? [];
      if (parts.every((part) => tokens.includes(part))) {
        return col;
      }
    }
  }

  for (const keyword of keywords) {
    const target = keyword.toLowerCase();
    for (const col of columns) {
      const tokens = tokenized.get(col) ?? [];
      if (tokens.includes(target)) {
        return col;
      }
    }
  }

  for (const keyword of keywords) {
    const target = keyword.toLowerCase();
    if (target.length < 4) continue;
    const idx = lowered.findIndex((col) => col.includes(target));
    if (idx >= 0) {
      return columns[idx];
    }
  }

  return null;
};

const suggestMapping = (detail: DatasetDetail | null) => {
  if (!detail) {
    return { entityType: 'event', fields: {} as Record<string, string | null> };
  }
  const columns = detail.columns ?? [];
  const name = (detail.name || detail.dataset || '').toLowerCase();

  let entityType = 'event';
  if (name.includes('crash') || name.includes('accident')) {
    entityType = 'crash';
  } else if (name.includes('wzdx') || name.includes('workzone') || name.includes('work_zone')) {
    entityType = 'workzone';
  }

  const isWorkzone = entityType === 'workzone';
  const isCrash = entityType === 'crash';
  const fields: Record<string, string | null> = {
    primary_id: findColumn(columns, ['crash_id', 'incident_id', 'event_id', 'record', 'uuid', 'image', 'image_no', 'id']),
    timestamp: findColumn(columns, ['timestamp', 'datetime', 'date_time']),
    event_date: isCrash ? findColumn(columns, ['accident_date', 'crash_date', 'event_date', 'date']) : null,
    event_time: isCrash ? findColumn(columns, ['accident_time', 'crash_time', 'event_time', 'time']) : null,
    start_time: isWorkzone ? findColumn(columns, ['start', 'begin', 'open_time']) : null,
    end_time: isWorkzone ? findColumn(columns, ['end', 'close_time', 'end_date']) : null,
    latitude: detail.geo?.lat_column ?? findColumn(columns, ['lat']),
    longitude: detail.geo?.lon_column ?? findColumn(columns, ['lon', 'lng', 'long']),
    geometry: detail.geo?.geometry_column ?? findColumn(columns, ['geometry', 'geom', 'geojson', 'wkt']),
    road_name: detail.road_match?.road_column ?? findColumn(columns, ['road', 'route', 'street']),
    road_id: detail.road_match?.road_segment_id_column ?? findColumn(columns, ['road_segment', 'segment_id', 'road_id']),
  };

  return { entityType, fields };
};

const datasetKey = (dataset: DatasetSummary): string => dataset.dataset_id ?? dataset.name;

const isSystemDataset = (dataset: DatasetSummary | null | undefined): boolean => {
  if (!dataset) return false;
  const entityType = String(dataset.entity_type ?? '').toLowerCase();
  const source = String(dataset.source ?? '').toLowerCase();
  const id = String(dataset.dataset_id ?? '').toLowerCase();
  return source === 'cv_runs' || entityType === 'cv' || entityType === 'hard_braking' || id.startsWith('cv_run:');
};

export const IngestionInspector = ({ onClose }: Props) => {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [uploadMode, setUploadMode] = useState<'file' | 'url'>('file');
  const [uploading, setUploading] = useState(false);
  const [uploadUrl, setUploadUrl] = useState('');
  const [uploadEntityType, setUploadEntityType] = useState('event');
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deleteStatus, setDeleteStatus] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletingDatasetId, setDeletingDatasetId] = useState<string | null>(null);
  const [showSystemDatasets, setShowSystemDatasets] = useState(false);
  const [mappingEntity, setMappingEntity] = useState('event');
  const [mappingFields, setMappingFields] = useState<Record<string, string | null>>({});
  const [mappingSaving, setMappingSaving] = useState(false);
  const [mappingStatus, setMappingStatus] = useState<string | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [codeMappingDraft, setCodeMappingDraft] = useState<Record<string, string | null>>({});
  const [codeMappingSaving, setCodeMappingSaving] = useState(false);
  const [codeMappingStatus, setCodeMappingStatus] = useState<string | null>(null);
  const [codeMappingError, setCodeMappingError] = useState<string | null>(null);
  const [queryableDraft, setQueryableDraft] = useState<Array<{
    query_name: string;
    source_column: string;
    enabled: boolean;
    locked: boolean;
  }>>([]);
  const [queryableSaving, setQueryableSaving] = useState(false);
  const [queryableStatus, setQueryableStatus] = useState<string | null>(null);
  const [queryableError, setQueryableError] = useState<string | null>(null);
  const [codeMappingQuery, setCodeMappingQuery] = useState('');
  const [showMappedCodeOnly, setShowMappedCodeOnly] = useState(false);
  const [showAllCodeMappings, setShowAllCodeMappings] = useState(false);
  const [showAllPreviewColumns, setShowAllPreviewColumns] = useState(false);

  const fetchDatasets = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listDatasets();
      const list = Array.isArray(data.datasets) ? data.datasets : [];
      setDatasets(list);
      const hasUploaded = list.some((item) => !isSystemDataset(item));
      const hasSystem = list.some((item) => isSystemDataset(item));
      if (!hasUploaded && hasSystem) {
        setShowSystemDatasets(true);
      }
      const availableKeys = new Set(list.map(datasetKey));
      const currentSelected = selected && availableKeys.has(selected) ? selected : null;
      const firstUploaded = list.find((item) => !isSystemDataset(item));
      const firstSystem = list.find((item) => isSystemDataset(item));
      const nextSelected = currentSelected ?? (firstUploaded ? datasetKey(firstUploaded) : (firstSystem ? datasetKey(firstSystem) : null));
      setSelected(nextSelected);
    } catch (err: any) {
      setError(err.message || 'Failed to load datasets');
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    setUploading(true);
    setUploadStatus('Uploading file...');
    setUploadError(null);
    try {
      const data = await uploadDatasetFile({ file, entity_type: uploadEntityType }) as any;
      const isCodebook = !data.dataset_id && data.codebook;
      if (isCodebook) {
        const inserted = data.codebook?.inserted;
        const attrs = data.codebook?.attributes;
        setUploadStatus(data.message || `Codebook uploaded (${attrs ?? inserted ?? '—'} entries)`);
        await fetchDatasets();
        return;
      }
      const datasetId = data.dataset_id;
      const datasetName = data.name || data.dataset;
      const rows = data.ingest?.rows_inserted ?? data.rows;
      setUploadStatus(`Uploaded ${datasetName} (${rows ?? '—'} rows)`);
      await fetchDatasets();
      if (datasetId) {
        setSelected(datasetId);
      }
    } catch (err: any) {
      setUploadError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleUrlUpload = async () => {
    if (!uploadUrl.trim()) {
      setUploadError('Enter a URL to load.');
      return;
    }
    setUploading(true);
    setUploadStatus('Fetching URL...');
    setUploadError(null);
    try {
      const data = await uploadDatasetUrl({ url: uploadUrl.trim(), entity_type: uploadEntityType }) as any;
      const isCodebook = !data.dataset_id && data.codebook;
      if (isCodebook) {
        const inserted = data.codebook?.inserted;
        const attrs = data.codebook?.attributes;
        setUploadStatus(data.message || `Codebook uploaded (${attrs ?? inserted ?? '—'} entries)`);
        await fetchDatasets();
        return;
      }
      const datasetId = data.dataset_id;
      const datasetName = data.name || data.dataset;
      const rows = data.ingest?.rows_inserted ?? data.rows;
      setUploadStatus(`Loaded ${datasetName} (${rows ?? '—'} rows)`);
      await fetchDatasets();
      if (datasetId) {
        setSelected(datasetId);
      }
    } catch (err: any) {
      setUploadError(err.message || 'URL load failed');
    } finally {
      setUploading(false);
    }
  };

  const fetchDetail = async (datasetId: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDatasetById(datasetId);
      setDetail(data as DatasetDetail);
    } catch (err: any) {
      setError(err.message || 'Failed to load dataset detail');
      setDetail(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDatasets();
  }, []);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    const selectedDataset = datasets.find((item) => datasetKey(item) === selected);
    if (selectedDataset && isSystemDataset(selectedDataset)) {
      setDetail(null);
      setError(null);
      return;
    }
    if (datasets.length > 0 && !selectedDataset) {
      setDetail(null);
      return;
    }
    fetchDetail(selected);
  }, [selected, datasets]);

  useEffect(() => {
    if (!datasets.length) return;
    const hasUploaded = datasets.some((item) => !isSystemDataset(item));
    const hasSystem = datasets.some((item) => isSystemDataset(item));
    if (!hasUploaded && hasSystem) {
      setShowSystemDatasets(true);
    }
  }, [datasets]);

  useEffect(() => {
    if (!selected) return;
    const current = datasets.find((item) => datasetKey(item) === selected);
    if (current && isSystemDataset(current)) {
      setShowSystemDatasets(true);
    }
  }, [selected, datasets]);

  useEffect(() => {
    if (!detail) return;
    const schemas = getEntitySchemas(detail);
    const suggestion = suggestMapping(detail);
    const savedFields = detail.mapping?.fields ?? {};
    const nextEntity = detail.mapping?.entity_type || suggestion.entityType;
    const normalizedEntity = schemas[nextEntity] ? nextEntity : 'event';
    setMappingEntity(normalizedEntity);
    setMappingFields({ ...suggestion.fields, ...savedFields });
    setMappingStatus(null);
    setMappingError(null);

    const codebook = detail.codebook ?? detail.stats?.codebook ?? {};
    const mappings = codebook?.mappings ?? {};
    const nextCodeDraft: Record<string, string | null> = {};
    Object.entries(mappings).forEach(([column, info]) => {
      if (typeof info === 'string') {
        nextCodeDraft[column] = info || null;
        return;
      }
      const meta = info as CodeMappingMeta;
      if (meta && typeof meta === 'object' && typeof meta.attr === 'string') {
        nextCodeDraft[column] = meta.attr || null;
      }
    });
    setCodeMappingDraft(nextCodeDraft);
    setCodeMappingStatus(null);
    setCodeMappingError(null);
    const nextQueryableDraft = (detail.queryable_fields?.fields ?? []).map((field) => ({
      query_name: String(field.query_name ?? '').trim(),
      source_column: String(field.source_column ?? '').trim(),
      enabled: Boolean(field.enabled ?? true),
      locked: Boolean(field.locked ?? false),
    })).filter((field) => field.query_name || field.source_column);
    setQueryableDraft(nextQueryableDraft);
    setQueryableStatus(null);
    setQueryableError(null);
    setCodeMappingQuery('');
    setShowMappedCodeOnly(false);
    setShowAllCodeMappings(false);
    setDeleteStatus(null);
    setDeleteError(null);
  }, [detail?.dataset_id]);

  const previewRows = detail?.preview_rows ?? [];
  const previewColumns = previewRows[0] ? Object.keys(previewRows[0]) : [];
  const visiblePreviewColumns = showAllPreviewColumns ? previewColumns : previewColumns.slice(0, 8);
  const autoMappingError = detail?.auto_mapping_error ?? undefined;
  const connections = detail?.connections;
  const mappingReadOnly = Boolean(connections?.mapped_from);
  const entitySchemas = getEntitySchemas(detail);
  const uploadedDatasets = datasets.filter((item) => !isSystemDataset(item));
  const systemDatasets = datasets.filter((item) => isSystemDataset(item));
  const selectedDatasetSummary = datasets.find((item) => datasetKey(item) === selected) ?? null;
  const selectedSystemDataset = selectedDatasetSummary && isSystemDataset(selectedDatasetSummary) ? selectedDatasetSummary : null;
  const entityOptions = Object.entries(entitySchemas).map(([key, schema]) => ({
    value: key,
    label: schema.label || key,
  }));
  const activeSchema = entitySchemas[mappingEntity] ?? entitySchemas.event ?? FALLBACK_ENTITY_SCHEMAS.event;
  const requiredMappingFields = (activeSchema?.required_fields ?? []).map(getFieldMeta);
  const optionalMappingFields = (activeSchema?.optional_fields ?? [])
    .map(getFieldMeta)
    .filter((field) => !requiredMappingFields.some((req) => req.key === field.key));
  const mappingStatusInfo = detail?.mapping_status;
  const detailWarnings = detail?.warnings ?? [];
  const mappingWarnings = mappingStatusInfo?.warnings ?? [];
  const combinedWarnings = Array.from(new Set([...detailWarnings, ...mappingWarnings]));

  const codebookInfo = detail?.codebook ?? detail?.stats?.codebook;
  const codebookAvailable = Boolean(codebookInfo?.available);
  const codebookMatches = codebookInfo?.matches ?? [];
  const codebookCandidates = codebookInfo?.missing?.length ? codebookInfo?.missing : codebookInfo?.candidates;
  const codebookMappedCount = codebookMatches.length;
  const codebookCandidateCount = Array.isArray(codebookCandidates) ? codebookCandidates.length : 0;
  const codebookAttributes = detail?.codebook_attributes ?? [];
  const codebookMappings = (codebookInfo?.mappings ?? {}) as Record<string, CodeMappingMeta | string>;
  const codebookMappingMeta: Record<string, CodeMappingMeta> = {};
  Object.entries(codebookMappings).forEach(([column, info]) => {
    if (typeof info === 'string') {
      codebookMappingMeta[column] = { attr: info };
    } else if (info && typeof info === 'object') {
      codebookMappingMeta[column] = info;
    }
  });
  const codeMappingColumns = Array.from(
    new Set([
      ...Object.keys(codebookMappingMeta),
      ...(codebookCandidates ?? []),
      ...Object.keys(codeMappingDraft),
    ])
  ).sort((a, b) => a.localeCompare(b));
  const normalizedCodeQuery = codeMappingQuery.trim().toLowerCase();
  const codeMappingItems = codeMappingColumns
    .map((column) => {
      const meta = codebookMappingMeta[column] || {};
      const selectedAttr = (codeMappingDraft[column] ?? meta.attr ?? '').trim();
      return {
        column,
        meta,
        selectedAttr,
        isMapped: Boolean(selectedAttr),
      };
    })
    .filter((item) => {
      if (showMappedCodeOnly && !item.isMapped) return false;
      if (!normalizedCodeQuery) return true;
      const haystack = `${item.column} ${item.selectedAttr}`.toLowerCase();
      return haystack.includes(normalizedCodeQuery);
    })
    .sort((a, b) => {
      if (a.isMapped !== b.isMapped) return a.isMapped ? -1 : 1;
      const aCoverage = Number.isFinite(a.meta.coverage) ? Number(a.meta.coverage) : -1;
      const bCoverage = Number.isFinite(b.meta.coverage) ? Number(b.meta.coverage) : -1;
      if (aCoverage !== bCoverage) return bCoverage - aCoverage;
      return a.column.localeCompare(b.column);
    });
  const CODE_MAPPING_PAGE_SIZE = 24;
  const visibleCodeMappingItems = showAllCodeMappings ? codeMappingItems : codeMappingItems.slice(0, CODE_MAPPING_PAGE_SIZE);
  const hiddenCodeMappingCount = Math.max(0, codeMappingItems.length - visibleCodeMappingItems.length);
  const queryableSourceOptions = Array.from(new Set([
    ...(detail?.columns ?? []),
    ...queryableDraft.map((field) => field.source_column).filter(Boolean),
  ])).sort((a, b) => a.localeCompare(b));

  const handleSaveMapping = async () => {
    if (!selected) return;
    if (mappingReadOnly) {
      setMappingError('Mapping edits are disabled for derived datasets.');
      setMappingStatus(null);
      return;
    }
    setMappingSaving(true);
    setMappingStatus('Saving mapping...');
    setMappingError(null);
    try {
      const normalizedFields = Object.fromEntries(
        Object.entries(mappingFields).map(([key, value]) => [key, value?.trim() ? value : null])
      );
      const data = await updateDatasetMapping(selected, {
        entity_type: mappingEntity,
        fields: normalizedFields,
      });
      setDetail(data as DatasetDetail);
      setMappingStatus('Mapping saved.');
    } catch (err: any) {
      setMappingError(err.message || 'Failed to save mapping');
      setMappingStatus(null);
    } finally {
      setMappingSaving(false);
    }
  };

  const handleSaveCodeMappings = async () => {
    if (!selected) return;
    setCodeMappingSaving(true);
    setCodeMappingStatus('Saving code mappings...');
    setCodeMappingError(null);
    try {
      const payloadMappings: Record<string, string | null> = {};
      codeMappingColumns.forEach((column) => {
        const value = codeMappingDraft[column];
        payloadMappings[column] = value && value.trim() ? value.trim() : null;
      });
      const data = await updateDatasetCodeMappings(selected, { mappings: payloadMappings });
      setDetail(data as DatasetDetail);
      setCodeMappingStatus('Code mappings saved.');
    } catch (err: any) {
      setCodeMappingError(err.message || 'Failed to save code mappings');
      setCodeMappingStatus(null);
    } finally {
      setCodeMappingSaving(false);
    }
  };

  const handleSaveQueryableFields = async () => {
    if (!selected) return;
    if (mappingReadOnly) {
      setQueryableError('Queryable fields are read-only for derived datasets.');
      setQueryableStatus(null);
      return;
    }
    setQueryableSaving(true);
    setQueryableStatus('Saving queryable fields...');
    setQueryableError(null);
    try {
      const payloadFields = queryableDraft.map((field) => ({
        query_name: field.query_name.trim() || null,
        source_column: field.source_column.trim() || null,
        enabled: field.locked ? true : Boolean(field.enabled),
      }));
      const data = await updateDatasetQueryableFields(selected, { fields: payloadFields });
      setDetail(data as DatasetDetail);
      setQueryableStatus('Queryable fields saved.');
    } catch (err: any) {
      setQueryableError(err.message || 'Failed to save queryable fields');
      setQueryableStatus(null);
    } finally {
      setQueryableSaving(false);
    }
  };

  const handleDeleteDataset = async () => {
    const datasetId = detail?.dataset_id ? String(detail.dataset_id) : '';
    if (!datasetId) return;
    const datasetName = String(detail?.name || detail?.dataset || datasetId);
    const confirmed = window.confirm(
      `Delete "${datasetName}"?\n\nThis will permanently remove the dataset and all uploaded rows from the backend.`
    );
    if (!confirmed) return;

    setDeletingDatasetId(datasetId);
    setDeleteStatus('Deleting dataset...');
    setDeleteError(null);
    try {
      const response = await deleteDatasetById(datasetId);
      const eventsDeleted = Number(response?.events_deleted ?? 0);
      const codebookDeleted = Number(response?.codebook_deleted ?? 0);
      if (codebookDeleted > 0) {
        setDeleteStatus(`Deleted "${datasetName}" (${eventsDeleted} rows, ${codebookDeleted} codebook entries removed).`);
      } else {
        setDeleteStatus(`Deleted "${datasetName}" (${eventsDeleted} rows removed).`);
      }
      setDetail(null);
      setSelected(null);
      await fetchDatasets();
    } catch (err: any) {
      setDeleteError(err.message || 'Failed to delete dataset');
      setDeleteStatus(null);
    } finally {
      setDeletingDatasetId(null);
    }
  };

  return (
    <div className="ingestion-panel">
      <div className="ingestion-header">
        <div>
          <div className="ingestion-kicker">Ingestion Inspector</div>
          <h2>Map Columns & Check Data</h2>
          <p>Confirm your column mapping and spot-check the sample rows.</p>
        </div>
        <div className="ingestion-header-actions">
          <button
            className="ingestion-refresh"
            onClick={() => setShowUpload((prev) => !prev)}
          >
            {showUpload ? 'Hide Upload' : 'Upload'}
          </button>
          <button className="ingestion-refresh" onClick={fetchDatasets}>Refresh</button>
          <button className="ingestion-close" onClick={onClose} aria-label="Close ingestion inspector">✕</button>
        </div>
      </div>

      {showUpload ? (
        <div className="ingestion-upload">
          <div className="ingestion-upload-tabs">
            <button
              className={`ingestion-upload-tab ${uploadMode === 'file' ? 'active' : ''}`}
              onClick={() => setUploadMode('file')}
            >
              Upload File
            </button>
            <button
              className={`ingestion-upload-tab ${uploadMode === 'url' ? 'active' : ''}`}
              onClick={() => setUploadMode('url')}
            >
              Upload via URL
            </button>
          </div>
          <div className="ingestion-upload-row">
            <label className="ingestion-upload-label" htmlFor="upload-entity-type">Entity type</label>
            <select
              id="upload-entity-type"
              className="ingestion-map-select ingestion-upload-select"
              value={uploadEntityType}
              onChange={(event) => setUploadEntityType(event.target.value)}
              disabled={uploading}
            >
              {UPLOAD_ENTITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          {uploadMode === 'file' ? (
            <div className="ingestion-upload-row">
              <input
                type="file"
                accept=".csv,.json,.geojson,.xml,.gml"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    handleFileUpload(file);
                    event.currentTarget.value = '';
                  }
                }}
                disabled={uploading}
              />
              <span className="ingestion-muted">CSV, JSON/GeoJSON, XML/GML</span>
            </div>
          ) : (
            <div className="ingestion-upload-row">
              <input
                className="ingestion-upload-input"
                type="text"
                placeholder="Paste JSON or GeoJSON URL"
                value={uploadUrl}
                onChange={(event) => setUploadUrl(event.target.value)}
                disabled={uploading}
              />
              <button
                className="ingestion-refresh"
                onClick={handleUrlUpload}
                disabled={uploading}
              >
                Load
              </button>
            </div>
          )}
          {uploadStatus ? <div className="ingestion-muted">{uploadStatus}</div> : null}
          {uploadError ? <div className="ingestion-error">{uploadError}</div> : null}
        </div>
      ) : null}

      <div className="ingestion-body">
        <aside className="ingestion-sidebar">
          <h3>Datasets</h3>
          {loading && datasets.length === 0 ? <div className="ingestion-muted">Loading…</div> : null}
          {!loading && datasets.length === 0 ? <div className="ingestion-muted">No datasets loaded.</div> : null}
          {!loading && uploadedDatasets.length === 0 && systemDatasets.length > 0 ? (
            <div className="ingestion-muted">
              CV segment data is listed under <strong>System (CV)</strong> below (click Show if hidden).
              Files in the <code>uploads/</code> folder must be ingested via Upload or the import script.
            </div>
          ) : null}
          <div className="dataset-list">
            {uploadedDatasets.length ? <div className="dataset-section-title">Uploaded</div> : null}
            {uploadedDatasets.map((ds) => {
              const datasetKey = ds.dataset_id ?? ds.name;
              const roadMatch = ds.road_match ?? ds.stats?.road_match;
              const matchRate =
                typeof roadMatch?.match_rate === 'number'
                  ? roadMatch.match_rate
                  : (roadMatch?.matched && roadMatch?.total ? roadMatch.matched / roadMatch.total : undefined);
              const rows = ds.row_count ?? ds.stats?.ingest?.rows_inserted;
              return (
                <button
                  key={datasetKey}
                  className={`dataset-item ${selected === datasetKey ? 'active' : ''}`}
                  onClick={() => setSelected(datasetKey)}
                >
                  <div className="dataset-title">{ds.name}</div>
                  <div className="dataset-meta">
                    <span>{rows ?? '—'} rows</span>
                    {typeof matchRate === 'number' ? <span>match {formatPercent(matchRate)}</span> : null}
                  </div>
                  {ds.dataset_id ? <div className="dataset-meta">id {ds.dataset_id}</div> : null}
                </button>
              );
            })}
            {systemDatasets.length ? (
              <div className="dataset-section-head">
                <span className="dataset-section-title">System (CV)</span>
                <button
                  className="dataset-section-toggle"
                  onClick={() => setShowSystemDatasets((prev) => !prev)}
                >
                  {showSystemDatasets ? 'Hide' : 'Show'}
                </button>
              </div>
            ) : null}
            {showSystemDatasets ? systemDatasets.map((ds) => {
              const key = datasetKey(ds);
              const points = ds.row_count ?? ds.stats?.ingest?.rows_inserted;
              return (
                <button
                  key={key}
                  className={`dataset-item ${selected === key ? 'active' : ''}`}
                  onClick={() => setSelected(key)}
                >
                  <div className="dataset-title">{ds.name}</div>
                  <div className="dataset-meta">
                    <span>{points ?? '—'} points</span>
                    <span>{ds.is_active ? 'active' : 'system'}</span>
                  </div>
                  {ds.dataset_id ? <div className="dataset-meta">id {ds.dataset_id}</div> : null}
                </button>
              );
            }) : null}
          </div>
        </aside>

        <section className="ingestion-content">
          {error ? <div className="ingestion-error">{error}</div> : null}
          {!detail && !selectedSystemDataset ? <div className="ingestion-muted">Select a dataset to view details.</div> : null}

          {selectedSystemDataset ? (
            <div className="ingestion-card full">
              <h4>System Dataset (Read-only)</h4>
              <div className="ingestion-row"><span>Name</span><span>{selectedSystemDataset.name}</span></div>
              <div className="ingestion-row"><span>Type</span><span>{selectedSystemDataset.entity_type ?? 'cv'}</span></div>
              <div className="ingestion-row"><span>ID</span><span className="ingestion-mono">{selectedSystemDataset.dataset_id ?? '—'}</span></div>
              <div className="ingestion-row"><span>Points</span><span>{selectedSystemDataset.row_count ?? '—'}</span></div>
              <div className="ingestion-row"><span>Run ID</span><span className="ingestion-mono">{selectedSystemDataset.run_id ?? '—'}</span></div>
              <div className="ingestion-row"><span>Time Range</span><span>{formatDateRangeHuman(selectedSystemDataset.ts_start, selectedSystemDataset.ts_end, '—')}</span></div>
              <div className="ingestion-row"><span>Status</span><span>{selectedSystemDataset.is_active ? 'Active CV run' : 'Available run'}</span></div>
              <div className="ingestion-muted">
                System CV datasets are managed in backend run tables. Mapping and codebook tools are only for uploaded datasets.
              </div>
            </div>
          ) : null}

          {detail ? (
            <>
              {combinedWarnings.length ? (
                <div className="ingestion-warning-list">
                  <div className="ingestion-warning">
                    {combinedWarnings[0]}
                    {combinedWarnings.length > 1 ? ` (+${combinedWarnings.length - 1} more)` : ''}
                  </div>
                </div>
              ) : null}
              <div className="ingestion-grid ingestion-grid-compact">
                <div className="ingestion-card">
                  <div className="ingestion-card-header">
                    <h4>Dataset Summary</h4>
                    <button
                      className="ingestion-refresh ingestion-refresh-danger"
                      onClick={handleDeleteDataset}
                      disabled={!detail.dataset_id || deletingDatasetId === String(detail.dataset_id ?? '')}
                      type="button"
                    >
                      {deletingDatasetId === String(detail.dataset_id ?? '') ? 'Deleting…' : 'Delete dataset'}
                    </button>
                  </div>
                  <div className="ingestion-row"><span>ID</span><span className="ingestion-mono">{detail.dataset_id ?? '—'}</span></div>
                  <div className="ingestion-row"><span>Entity</span><span>{detail.entity_type ?? '—'}</span></div>
                  <div className="ingestion-row"><span>Rows</span><span>{detail.row_count ?? detail.stats?.ingest?.rows_inserted ?? '—'}</span></div>
                  <div className="ingestion-row"><span>Columns</span><span>{detail.columns?.length ?? '—'}</span></div>
                  <div className="ingestion-row"><span>Ingested</span><span>{formatDateTimeHuman(detail.ingested_at ?? detail.created_at, '—')}</span></div>
                  <div className="ingestion-row"><span>Source</span><span className="ingestion-mono">{detail.source ?? '—'}</span></div>
                  <div className="ingestion-row">
                    <span>Mapping Status</span>
                    <span className={`ingestion-chip ${mappingStatusInfo?.level || 'unmapped'}`}>
                      {mappingStatusInfo?.message || 'Not evaluated'}
                    </span>
                  </div>
                  <div className="ingestion-row">
                    <span>Mapping Coverage</span>
                    <span>{formatPercent(mappingStatusInfo?.completion_ratio)}</span>
                  </div>
                  {deleteStatus ? <div className="ingestion-muted">{deleteStatus}</div> : null}
                  {deleteError ? <div className="ingestion-error">{deleteError}</div> : null}
                </div>
              </div>

              <details className="ingestion-collapsible">
                <summary>Data diagnostics</summary>
                <div className="ingestion-grid">
                  <div className="ingestion-card">
                    <h4>Location & Road Match</h4>
                    {(() => {
                      const roadMatch = detail.road_match ?? detail.stats?.road_match;
                      const matchRate =
                        typeof roadMatch?.match_rate === 'number'
                          ? roadMatch.match_rate
                          : (roadMatch?.matched && roadMatch?.total ? roadMatch.matched / roadMatch.total : undefined);
                      return (
                        <>
                          <div className="ingestion-row"><span>Latitude</span><span>{detail.geo?.lat_column ?? '—'}</span></div>
                          <div className="ingestion-row"><span>Longitude</span><span>{detail.geo?.lon_column ?? '—'}</span></div>
                          <div className="ingestion-row"><span>Road Column</span><span>{detail.road_match?.road_column ?? '—'}</span></div>
                          <div className="ingestion-row"><span>Road Match Rate</span><span>{formatPercent(matchRate)}</span></div>
                          <div className="ingestion-row"><span>Matched / Unmatched</span><span>{roadMatch?.matched ?? '—'} / {roadMatch?.unmatched ?? (roadMatch?.total ? (roadMatch.total - (roadMatch.matched ?? 0)) : '—')}</span></div>
                        </>
                      );
                    })()}
                  </div>

                  <div className="ingestion-card">
                    <h4>Codebook</h4>
                    <div className="ingestion-row"><span>Available</span><span>{codebookAvailable ? 'Yes' : 'No'}</span></div>
                    <div className="ingestion-row"><span>Attributes</span><span>{codebookAttributes.length || codebookInfo?.attributes || 0}</span></div>
                    <div className="ingestion-row"><span>Mapped columns</span><span>{codebookMappedCount}</span></div>
                    <div className="ingestion-row"><span>Coded candidates</span><span>{codebookCandidateCount}</span></div>
                    {!codebookAvailable && codebookCandidates?.length ? (
                      <div className="ingestion-muted">Upload an attributes/codebook file to decode these columns.</div>
                    ) : null}
                  </div>
                </div>
              </details>

              <div className="ingestion-card full mapping">
                <div className="ingestion-card-header">
                  <div>
                    <h4>Queryable Fields</h4>
                    <div className="ingestion-muted">
                      Only enabled query names are available to the agent. Add a source column and give it a query name.
                    </div>
                  </div>
                  <div className="ingestion-map-actions">
                    <button
                      className="ingestion-refresh"
                      onClick={() => setQueryableDraft((prev) => [
                        ...prev,
                        { query_name: '', source_column: '', enabled: true, locked: false },
                      ])}
                      disabled={queryableSaving || mappingReadOnly}
                    >
                      Add field
                    </button>
                    <button
                      className="ingestion-refresh"
                      onClick={handleSaveQueryableFields}
                      disabled={queryableSaving || mappingReadOnly}
                    >
                      {queryableSaving ? 'Saving…' : 'Save queryable fields'}
                    </button>
                  </div>
                </div>
                {mappingReadOnly ? (
                  <div className="ingestion-muted">
                    Queryable fields are locked because this dataset came from {connections?.mapped_from}.
                  </div>
                ) : null}
                {queryableDraft.length === 0 ? (
                  <div className="ingestion-muted">No queryable fields configured yet.</div>
                ) : (
                  <div className="ingestion-queryable-list">
                    {queryableDraft.map((field, idx) => (
                      <div className="ingestion-queryable-row" key={`queryable-${idx}`}>
                        <input
                          className="ingestion-upload-input ingestion-queryable-input"
                          type="text"
                          value={field.query_name}
                          placeholder="Query name (e.g., crash_type)"
                          onChange={(event) => {
                            const next = [...queryableDraft];
                            next[idx] = { ...field, query_name: event.target.value };
                            setQueryableDraft(next);
                            setQueryableStatus(null);
                            setQueryableError(null);
                          }}
                          disabled={queryableSaving || mappingReadOnly}
                        />
                        <select
                          className="ingestion-map-select ingestion-queryable-select"
                          value={field.source_column}
                          onChange={(event) => {
                            const next = [...queryableDraft];
                            next[idx] = { ...field, source_column: event.target.value };
                            setQueryableDraft(next);
                            setQueryableStatus(null);
                            setQueryableError(null);
                          }}
                          disabled={queryableSaving || mappingReadOnly}
                        >
                          <option value="">Select source column</option>
                          {queryableSourceOptions.map((col) => (
                            <option key={`queryable-source-${idx}-${col}`} value={col}>{col}</option>
                          ))}
                        </select>
                        <label className="ingestion-queryable-flag">
                          <input
                            type="checkbox"
                            checked={field.locked ? true : field.enabled}
                            onChange={(event) => {
                              const next = [...queryableDraft];
                              next[idx] = { ...field, enabled: event.target.checked };
                              setQueryableDraft(next);
                              setQueryableStatus(null);
                              setQueryableError(null);
                            }}
                            disabled={queryableSaving || mappingReadOnly || field.locked}
                          />
                          {field.locked ? 'Required' : 'Enabled'}
                        </label>
                        {!field.locked ? (
                          <button
                            className="ingestion-close"
                            onClick={() => {
                              setQueryableDraft((prev) => prev.filter((_, i) => i !== idx));
                              setQueryableStatus(null);
                              setQueryableError(null);
                            }}
                            disabled={queryableSaving || mappingReadOnly}
                            aria-label="Remove queryable field"
                            type="button"
                          >
                            ✕
                          </button>
                        ) : (
                          <span className="ingestion-muted">Locked</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {queryableStatus ? <div className="ingestion-muted">{queryableStatus}</div> : null}
                {queryableError ? <div className="ingestion-error">{queryableError}</div> : null}
              </div>

              <div className="ingestion-card full mapping">
                <div className="ingestion-card-header">
                  <div>
                    <h4>Column Mapping</h4>
                    <div className="ingestion-muted">
                      {mappingReadOnly ? 'Mapping is read-only for derived datasets.' : 'Choose which columns match each field below.'}
                    </div>
                  </div>
                  <div className="ingestion-map-actions">
                    <button
                      className="ingestion-refresh"
                      onClick={handleSaveMapping}
                      disabled={mappingSaving || mappingReadOnly}
                    >
                      {mappingSaving ? 'Saving…' : 'Save mapping'}
                    </button>
                  </div>
                </div>
                {mappingReadOnly ? (
                  <div className="ingestion-muted">
                    Mapping is locked because this dataset came from {connections?.mapped_from}.
                  </div>
                ) : null}
                <div className="ingestion-map-grid">
                  <div className="ingestion-map-item">
                    <label>Entity Type</label>
                    <select
                      className="ingestion-map-select"
                      value={mappingEntity}
                      onChange={(event) => {
                        setMappingEntity(event.target.value);
                        setMappingStatus(null);
                        setMappingError(null);
                      }}
                      disabled={mappingReadOnly}
                    >
                      {entityOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                  {requiredMappingFields.map((field) => (
                    <div className="ingestion-map-item" key={field.key}>
                      <label>{field.label} (Required)</label>
                      <select
                        className="ingestion-map-select"
                        value={mappingFields[field.key] ?? ''}
                        onChange={(event) => {
                          setMappingFields((prev) => ({
                            ...prev,
                            [field.key]: event.target.value || null,
                          }));
                          setMappingStatus(null);
                          setMappingError(null);
                        }}
                        disabled={mappingReadOnly}
                      >
                        <option value="">Not mapped</option>
                        {detail.columns?.map((col) => (
                          <option key={`${field.key}-${col}`} value={col}>{col}</option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
                {optionalMappingFields.length ? (
                  <details className="ingestion-collapsible">
                    <summary>Optional mappings ({optionalMappingFields.length})</summary>
                    <div className="ingestion-map-grid">
                      {optionalMappingFields.map((field) => (
                        <div className="ingestion-map-item" key={field.key}>
                          <label>{field.label}</label>
                          <select
                            className="ingestion-map-select"
                            value={mappingFields[field.key] ?? ''}
                            onChange={(event) => {
                              setMappingFields((prev) => ({
                                ...prev,
                                [field.key]: event.target.value || null,
                              }));
                              setMappingStatus(null);
                              setMappingError(null);
                            }}
                            disabled={mappingReadOnly}
                          >
                            <option value="">Not mapped</option>
                            {detail.columns?.map((col) => (
                              <option key={`${field.key}-${col}`} value={col}>{col}</option>
                            ))}
                          </select>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}
                {mappingStatusInfo?.missing_required?.length ? (
                  <div className="ingestion-warning">
                    Missing required fields: {mappingStatusInfo.missing_required.join(', ')}
                  </div>
                ) : null}
                {mappingStatus ? <div className="ingestion-muted">{mappingStatus}</div> : null}
                {mappingError ? <div className="ingestion-error">{mappingError}</div> : null}
                {autoMappingError ? (
                  <div className="ingestion-error">Auto mapping failed: {autoMappingError}</div>
                ) : null}
              </div>

              <div className="ingestion-card full mapping">
                <details className="ingestion-collapsible">
                  <summary>Code mappings</summary>
                  <div className="ingestion-card-header">
                    <div>
                      <h4>Code Mappings</h4>
                      <div className="ingestion-muted">
                        Map coded columns to codebook attributes. Exact matches are preferred; normalized suggestions are fallback.
                      </div>
                    </div>
                    <div className="ingestion-map-actions">
                      <button
                        className="ingestion-toggle"
                        onClick={() => setShowMappedCodeOnly((prev) => !prev)}
                        type="button"
                      >
                        {showMappedCodeOnly ? 'Show all columns' : 'Show mapped only'}
                      </button>
                      <button
                        className="ingestion-refresh"
                        onClick={handleSaveCodeMappings}
                        disabled={codeMappingSaving || !codebookAttributes.length}
                      >
                        {codeMappingSaving ? 'Saving…' : 'Save code mappings'}
                      </button>
                    </div>
                  </div>
                  {!codebookAttributes.length ? (
                    <div className="ingestion-muted">No codebook attributes loaded in this session.</div>
                  ) : codeMappingColumns.length === 0 ? (
                    <div className="ingestion-muted">No coded column candidates detected yet.</div>
                  ) : (
                    <>
                      <div className="ingestion-code-controls">
                        <input
                          className="ingestion-upload-input ingestion-code-search"
                          type="text"
                          value={codeMappingQuery}
                          onChange={(event) => setCodeMappingQuery(event.target.value)}
                          placeholder="Search column or mapped attribute..."
                        />
                        <div className="ingestion-muted">
                          Showing {visibleCodeMappingItems.length} of {codeMappingItems.length}
                        </div>
                      </div>
                      <div className="ingestion-code-grid">
                        {visibleCodeMappingItems.map(({ column, meta, selectedAttr }) => (
                          <div className="ingestion-code-item" key={`code-map-${column}`}>
                            <label>{column}</label>
                            <select
                              className="ingestion-map-select"
                              value={selectedAttr}
                              onChange={(event) => {
                                setCodeMappingDraft((prev) => ({
                                  ...prev,
                                  [column]: event.target.value || null,
                                }));
                                setCodeMappingStatus(null);
                                setCodeMappingError(null);
                              }}
                            >
                              <option value="">Not mapped</option>
                              {codebookAttributes.map((attr) => (
                                <option key={`${column}-${attr}`} value={attr}>
                                  {attr}
                                </option>
                              ))}
                            </select>
                            <div className="ingestion-code-meta">
                              <span>method {meta.method ?? '—'}</span>
                              <span>coverage {formatPercent(meta.coverage)}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                      {hiddenCodeMappingCount > 0 ? (
                        <div className="ingestion-code-footer">
                          <button
                            className="ingestion-toggle"
                            onClick={() => setShowAllCodeMappings((prev) => !prev)}
                            type="button"
                          >
                            {showAllCodeMappings ? 'Show fewer' : `Show ${hiddenCodeMappingCount} more`}
                          </button>
                        </div>
                      ) : null}
                    </>
                  )}
                  {codeMappingStatus ? <div className="ingestion-muted">{codeMappingStatus}</div> : null}
                  {codeMappingError ? <div className="ingestion-error">{codeMappingError}</div> : null}
                </details>
              </div>

              <div className="ingestion-card full">
                <details className="ingestion-collapsible">
                  <summary>Sample data</summary>
                  <div className="ingestion-card-header">
                    <h4>Sample Data</h4>
                    {previewColumns.length > 8 && (
                      <button
                        className="ingestion-toggle"
                        onClick={() => setShowAllPreviewColumns(!showAllPreviewColumns)}
                      >
                        {showAllPreviewColumns ? 'Show fewer columns' : 'Show all columns'}
                      </button>
                    )}
                  </div>
                  {previewRows.length === 0 ? (
                    <div className="ingestion-muted">No preview rows available.</div>
                  ) : (
                    <div
                      className="ingestion-table preview"
                      style={{ ['--preview-columns' as any]: visiblePreviewColumns.length }}
                    >
                      <div className="ingestion-table-row ingestion-table-head preview">
                        {visiblePreviewColumns.map((col) => (
                          <span key={col}>{col}</span>
                        ))}
                      </div>
                      {previewRows.map((row, idx) => (
                        <div className="ingestion-table-row preview" key={`row-${idx}`}>
                          {visiblePreviewColumns.map((col) => (
                            <span className="ingestion-mono" key={`${idx}-${col}`}>
                              {formatPreviewValue(row[col])}
                            </span>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </details>
              </div>
            </>
          ) : null}
        </section>
      </div>
    </div>
  );
};

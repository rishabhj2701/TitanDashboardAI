import { useCallback, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { ProcessedVehicleData } from '../services/dataLoader';
import { analyzeCrash, analyzeWorkzone } from '../api/analysisClient';
import { clearChat, sendChat } from '../api/chatClient';
import { getDatasetById } from '../api/ingestionClient';
import { getSessionId } from '../api/session';
import { mapServerSelectionPoints, mapServerWorkzoneLines } from '../features/chat/mappers';
import type {
  ChatMessage,
  CrashAnalyzeDetail,
  UploadedFileData,
  WorkzoneAnalyzeDetail,
  WorkzoneLinePayload,
} from '../features/chat/types';

type UseChatSendParams = {
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  uploadedFileData: UploadedFileData | null;
  onVisualize: (type: string) => void;
  onShowChart: (type: 'speed' | 'road' | 'violations') => void;
  onChartPayload?: (payloads: unknown[]) => void;
  onServerSelection?: (points: ProcessedVehicleData[], overlay?: boolean) => void;
  onWorkzoneLines?: (lines: WorkzoneLinePayload[]) => void;
  onRoadAggregateFilter?: (filter: { road_name?: string; road_segment_id?: string; road_names?: string[]; min_points?: number; limit?: number }) => void;
  onClearHistory?: () => void;
  resetUploadState: () => void;
  chatStorageKey: string;
};

type UseChatSendResult = {
  handleSend: (userMessage: string) => Promise<void>;
  handleCrashAnalyze: (detail: CrashAnalyzeDetail) => Promise<void>;
  handleWorkzoneAnalyze: (detail: WorkzoneAnalyzeDetail) => Promise<void>;
};

type QueryableField = {
  queryName: string;
  sourceColumn: string;
  enabled: boolean;
  locked: boolean;
};

const normalizeStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value.map((v) => String(v));
};

const normalizeQueryableFields = (value: unknown): QueryableField[] => {
  const obj = (value && typeof value === 'object') ? (value as Record<string, unknown>) : {};
  const fields = Array.isArray(obj.fields) ? obj.fields : [];
  return fields
    .map((field) => {
      const item = (field && typeof field === 'object') ? (field as Record<string, unknown>) : {};
      const queryName = String(item.query_name ?? '').trim();
      const sourceColumn = String(item.source_column ?? '').trim();
      if (!queryName || !sourceColumn) return null;
      return {
        queryName,
        sourceColumn,
        enabled: Boolean(item.enabled ?? true),
        locked: Boolean(item.locked ?? false),
      };
    })
    .filter((item): item is QueryableField => item !== null);
};

export const useChatSend = ({
  messages,
  setMessages,
  setIsLoading,
  uploadedFileData,
  onVisualize,
  onShowChart,
  onChartPayload,
  onServerSelection,
  onWorkzoneLines,
  onRoadAggregateFilter,
  onClearHistory,
  resetUploadState,
  chatStorageKey,
}: UseChatSendParams): UseChatSendResult => {
  const handleCrashAnalyze = useCallback(async (detail: CrashAnalyzeDetail) => {
    if (!detail || !Number.isFinite(Number(detail.crash_lat)) || !Number.isFinite(Number(detail.crash_lon))) {
      return;
    }

    const distanceMeters = detail.distance_m ?? 200;
    const windowMinutes = detail.window_minutes ?? 60;
    const crashLabel = detail.crash_id ? `Crash ${detail.crash_id}` : 'Crash';

    setMessages(prev => [...prev, {
      role: 'user',
      content: `Analyze this crash (${crashLabel}, +/-${windowMinutes} min, ${distanceMeters}m).`
    }]);
    setIsLoading(true);

    try {
      const data = await analyzeCrash({
        crash_lat: detail.crash_lat,
        crash_lon: detail.crash_lon,
        crash_ts: detail.crash_ts ?? null,
        accident_date: detail.accident_date ?? null,
        accident_time: detail.accident_time ?? null,
        severity: detail.severity ?? null,
        road_segment_id: detail.road_segment_id ?? null,
        crash_id: detail.crash_id ?? null,
        distance_m: distanceMeters,
        window_minutes: windowMinutes,
        cv_dataset_id: detail.cv_dataset_id ?? null,
      });
      const assistantReply = data.response || 'Crash analysis complete.';
      setMessages(prev => [...prev, { role: 'assistant', content: assistantReply }]);

      const mapSelection = data?.mapSelection;
      const serverPoints = mapSelection?.points;
      if (Array.isArray(serverPoints) && onServerSelection) {
        const selectionPoints = mapServerSelectionPoints(serverPoints);

        const nonzero = selectionPoints.find(
          (row) => Math.abs(row.acceleration?.x ?? 0) > 0.0001 || Math.abs(row.acceleration?.y ?? 0) > 0.0001
        );
        const sample = nonzero ?? selectionPoints[0];
        if (sample) {
          console.debug('[CrashAnalysis] accel sample', {
            total: selectionPoints.length,
            sampleId: sample.id,
            accX: sample.acceleration?.x ?? null,
            accY: sample.acceleration?.y ?? null,
            speed: sample.speed,
            type: sample.type,
          });
        }

        const validPoints = selectionPoints.filter(
          (p) => Number.isFinite(p.longitude) && Number.isFinite(p.latitude)
        );

        if (validPoints.length > 0) {
          onServerSelection?.(validPoints, mapSelection?.overlay);
        } else {
          onServerSelection?.([], mapSelection?.overlay);
          console.warn('[Map] Crash analysis returned no valid coordinates');
        }
      }

      if (Array.isArray(data?.chartPayload) && data.chartPayload.length > 0) {
        onChartPayload?.(data.chartPayload);
      }
    } catch (error: any) {
      console.error('Crash analysis error:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Crash analysis failed: ${error.message || 'Please check the backend logs.'}`
      }]);
    } finally {
      setIsLoading(false);
    }
  }, [onChartPayload, onServerSelection, setMessages, setIsLoading]);

  const handleWorkzoneAnalyze = useCallback(async (detail: WorkzoneAnalyzeDetail) => {
    if (!detail || !detail.workzone_id) {
      return;
    }

    const distanceMeters = detail.distance_m ?? 200;
    const wzLabel = detail.workzone_id ? `Workzone ${detail.workzone_id}` : 'Workzone';

    setMessages(prev => [...prev, {
      role: 'user',
      content: `Analyze this workzone (${wzLabel}, ${distanceMeters}m).`
    }]);
    setIsLoading(true);

    try {
      const data = await analyzeWorkzone({
        workzone_id: detail.workzone_id,
        road_segment_id: detail.road_segment_id ?? null,
        start_date: detail.start_date ?? null,
        end_date: detail.end_date ?? null,
        dataset_id: detail.dataset_id ?? null,
        cv_dataset_id: detail.cv_dataset_id ?? null,
        distance_m: distanceMeters,
      });
      const assistantReply = data.response || 'Workzone analysis complete.';
      setMessages(prev => [...prev, { role: 'assistant', content: assistantReply }]);

      const mapSelection = data?.mapSelection;
      const serverPoints = mapSelection?.points;
      if (Array.isArray(serverPoints) && onServerSelection) {
        const selectionPoints = mapServerSelectionPoints(serverPoints);

        const nonzero = selectionPoints.find(
          (row) => Math.abs(row.acceleration?.x ?? 0) > 0.0001 || Math.abs(row.acceleration?.y ?? 0) > 0.0001
        );
        const sample = nonzero ?? selectionPoints[0];
        if (sample) {
          console.debug('[WorkzoneAnalysis] accel sample', {
            total: selectionPoints.length,
            sampleId: sample.id,
            accX: sample.acceleration?.x ?? null,
            accY: sample.acceleration?.y ?? null,
            speed: sample.speed,
            type: sample.type,
          });
        }

        const validPoints = selectionPoints.filter(
          (p) => Number.isFinite(p.longitude) && Number.isFinite(p.latitude)
        );

        if (validPoints.length > 0) {
          onServerSelection?.(validPoints, mapSelection?.overlay);
        } else {
          onServerSelection?.([], mapSelection?.overlay);
          console.warn('[Map] Workzone analysis returned no valid coordinates');
        }
      }

      if (Array.isArray(mapSelection?.lines)) {
        onWorkzoneLines?.(mapServerWorkzoneLines(mapSelection.lines));
      }

      if (Array.isArray(data?.chartPayload) && data.chartPayload.length > 0) {
        onChartPayload?.(data.chartPayload);
      }
    } catch (error: any) {
      console.error('Workzone analysis error:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Workzone analysis failed: ${error.message || 'Please check the backend logs.'}`
      }]);
    } finally {
      setIsLoading(false);
    }
  }, [onChartPayload, onServerSelection, onWorkzoneLines, setMessages, setIsLoading]);

  // Listen for custom events
  useEffect(() => {
    const handleCrashEvent = (event: Event) => {
      const detail = (event as CustomEvent<CrashAnalyzeDetail>).detail;
      if (detail) handleCrashAnalyze(detail);
    };
    window.addEventListener('crash-analyze', handleCrashEvent as EventListener);
    return () => window.removeEventListener('crash-analyze', handleCrashEvent as EventListener);
  }, [handleCrashAnalyze]);

  useEffect(() => {
    const handleWzEvent = (event: Event) => {
      const detail = (event as CustomEvent<WorkzoneAnalyzeDetail>).detail;
      if (detail) handleWorkzoneAnalyze(detail);
    };
    window.addEventListener('workzone-analyze', handleWzEvent as EventListener);
    return () => window.removeEventListener('workzone-analyze', handleWzEvent as EventListener);
  }, [handleWorkzoneAnalyze]);

  const handleSend = useCallback(async (userMessage: string) => {
    if (!userMessage.trim()) return;

    const normalizedUserMessage = userMessage.toLowerCase().replace(/\s+/g, ' ').trim();
    const isClearHistoryCommand = /^(clear( all)? history|clear chat history|reset history)\b/.test(normalizedUserMessage);

    if (isClearHistoryCommand) {
      setIsLoading(true);
      try {
        await clearChat({ sessionId: getSessionId() });
      } catch (error) {
        console.error('Failed to clear backend history:', error);
      } finally {
        setMessages([]);
        sessionStorage.removeItem(chatStorageKey);
        resetUploadState();
        onClearHistory?.();
        setIsLoading(false);
      }
      return;
    }

    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsLoading(true);

    try {
      const lower = userMessage.toLowerCase();
      const wantsSampleContext = /\b(sample|example row|preview row|show row|debug context|full context)\b/.test(lower);
      let fileContext = '';
      if (uploadedFileData) {
        let totalRows = uploadedFileData.rowCount;
        const datasetLabel = uploadedFileData.datasetName || uploadedFileData.fileName;
        const datasetId = uploadedFileData.datasetId || '';
        const formatLabel = uploadedFileData.fileType === 'GeoJSON' ? 'geospatial' : 'tabular';
        let rawColumns = uploadedFileData.columns || [];
        let queryableFields = uploadedFileData.queryableFields || [];

        // Pull fresh dataset metadata so chat context reflects recent Ingestion edits
        // (e.g., queryable fields saved moments ago).
        if (datasetId) {
          try {
            const detail = await getDatasetById(datasetId);
            const refreshedQueryable = normalizeQueryableFields(
              (detail as Record<string, unknown>)?.queryable_fields
            );
            const refreshedColumns = normalizeStringArray(
              (detail as Record<string, unknown>)?.columns
            );
            const refreshedRows = Number((detail as Record<string, unknown>)?.row_count);
            if (refreshedQueryable.length) queryableFields = refreshedQueryable;
            if (refreshedColumns.length) rawColumns = refreshedColumns;
            if (Number.isFinite(refreshedRows) && refreshedRows > 0) totalRows = refreshedRows;
          } catch (contextErr) {
            console.warn('Failed to refresh dataset context for chat:', contextErr);
          }
        }

        const enabledQueryable = (queryableFields || []).filter((field) => field.enabled);
        const columns = enabledQueryable.length
          ? enabledQueryable.map((field) => field.queryName)
          : rawColumns;
        const loweredColumns = new Set(columns.map((col) => col.toLowerCase()));
        const importantColumns = columns.filter((col) => {
          const normalized = col.toLowerCase();
          return (
            ['ts', 'time', 'timestamp', 'event_date', 'event_time', 'accident_date', 'accident_time', 'start_date', 'end_date'].includes(normalized) ||
            ['lat', 'latitude', 'lon', 'longitude', 'geom', 'geometry'].includes(normalized) ||
            ['road', 'road_name', 'road_segment_id', 'way_id'].includes(normalized) ||
            ['speed', 'speedlimitmph', 'speed_over_limit', 'acc_x', 'acc_y'].includes(normalized) ||
            ['near_crash', 'near_workzone', 'number_killed', 'accident_type', 'severity'].includes(normalized)
          );
        });
        const keyColumns = importantColumns.length > 0 ? importantColumns.slice(0, 20) : columns.slice(0, 20);
        const matchedRoadNote = 'Note: If road matching is enabled, use `road`/`road_name` for matched road names (from roads.name via road_segment_id).';
        const queryableNote = enabledQueryable.length
          ? 'Queryable fields policy: Only fields listed above can be used in analysis. If needed, ask the user to open Ingestion > Queryable Fields to add and enable more.'
          : 'Queryable fields policy: this dataset has no explicit queryable override; use listed columns carefully.';
        const spatialHint = loweredColumns.has('lat') || loweredColumns.has('latitude') || loweredColumns.has('geom') || loweredColumns.has('geometry')
          ? 'Spatial: coordinates/geometry detected.'
          : 'Spatial: no obvious coordinate columns detected.';
        const temporalHint =
          loweredColumns.has('ts') ||
          loweredColumns.has('timestamp') ||
          loweredColumns.has('time') ||
          loweredColumns.has('event_date') ||
          loweredColumns.has('event_time') ||
          loweredColumns.has('accident_date') ||
          loweredColumns.has('accident_time') ||
          loweredColumns.has('start_date')
          ? 'Temporal: time/date columns detected.'
          : 'Temporal: no obvious time/date columns detected.';

        let sampleSection = '';
        if (wantsSampleContext) {
          if (uploadedFileData.fileType === 'GeoJSON' || uploadedFileData.fileName.endsWith('.json')) {
            const firstRow = uploadedFileData.data[0] as any;
            if (firstRow && firstRow.features && Array.isArray(firstRow.features) && firstRow.features.length > 0) {
              const props = firstRow.features[0].properties || {};
              const sampleProps: Record<string, unknown> = {};
              Object.keys(props).slice(0, 8).forEach((key) => {
                sampleProps[key] = props[key];
              });
              sampleSection = `\nSample feature properties:\n${JSON.stringify(sampleProps, null, 2)}`;
            }
          } else {
            const sampleRow = uploadedFileData.data[0];
            if (sampleRow && typeof sampleRow === 'object') {
              sampleSection = `\nSample row:\n${JSON.stringify(sampleRow, null, 2)}`;
            }
          }
        }

        fileContext = [
          '',
          '',
          '[Dataset Context]',
          `Dataset: ${datasetLabel}`,
          `Dataset ID: ${datasetId || 'unknown'}`,
          `Format: ${formatLabel}`,
          `Total Rows: ${totalRows}`,
          `Columns (${columns.length}): ${columns.join(', ')}`,
          `Key Columns: ${keyColumns.join(', ') || 'n/a'}`,
          spatialHint,
          temporalHint,
          matchedRoadNote,
          queryableNote,
          'If the question is ambiguous enough to change SQL results, ask one concise clarifying question before running analysis.',
          sampleSection,
        ].join('\n');
      }

      const data = await sendChat({
        message: userMessage + fileContext,
        sessionId: getSessionId(),
        history: messages,
        fileData: uploadedFileData
      });

      const assistantReply = data.responseText || data.response || '';
      const chartPayloads = Array.isArray(data.chartPayload) ? data.chartPayload : [];
      const mapSelection = data.mapSelection;
      const mapSelectionPoints = Array.isArray(mapSelection?.points) ? mapSelection.points : [];
      const mapSelectionLines = Array.isArray(mapSelection?.lines) ? mapSelection.lines : [];

      const hasServerLines = mapSelectionLines.length > 0;
      const roadAggregateFilter = mapSelection?.roadAggregateFilter;
      const hasServerPoints = mapSelectionPoints.length > 0;

      if (!data || !assistantReply) {
        throw new Error('Invalid response from server');
      }

      let actionTaken = false;

      const hasGenericPlot = (
        (lower.includes('plot') && (lower.includes('these') || lower.includes('this') || lower.includes('them') || lower.includes('it'))) ||
        (lower.includes('chart') && (lower.includes('these') || lower.includes('this') || lower.includes('them') || lower.includes('it'))) ||
        (lower.includes('visualize') && (lower.includes('these') || lower.includes('this') || lower.includes('them') || lower.includes('it'))) ||
        lower.includes('show chart') ||
        lower.includes('show graph')
      );

      const hasSpeedChart = (
        lower.includes('speed distribution') ||
        lower.includes('plot speed') ||
        lower.includes('show speed') ||
        (lower.includes('plot') && lower.includes('speed')) ||
        (lower.includes('chart') && lower.includes('speed'))
      );

      if (!chartPayloads.length) {
        if (hasSpeedChart || hasGenericPlot) {
          setTimeout(() => onShowChart('speed'), 500);
          actionTaken = true;
        } else if (lower.includes('road distribution') || lower.includes('by road')) {
          setTimeout(() => onShowChart('road'), 500);
          actionTaken = true;
        } else if (lower.includes('violation') && (lower.includes('plot') || lower.includes('show'))) {
          setTimeout(() => onShowChart('violations'), 500);
          actionTaken = true;
        }
      }

      const hasResetCommand = (
        lower.includes('show all') ||
        lower.includes('reset map') ||
        lower.includes('reset') ||
        lower.includes('clear filter') ||
        lower.includes('show everything') ||
        lower.includes('clear map')
      );

      if (hasResetCommand) {
        onVisualize('all');
        actionTaken = true;
      }

      const responseText = actionTaken ? `${assistantReply} \u2713` : assistantReply;
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: responseText,
        charts: chartPayloads.length > 0 ? chartPayloads as any : undefined,
      }]);

      const normalizedRoadAggregateFilter = roadAggregateFilter
        ? {
            road_name: typeof roadAggregateFilter.road_name === 'string' ? roadAggregateFilter.road_name : undefined,
            road_segment_id: typeof roadAggregateFilter.road_segment_id === 'string' ? roadAggregateFilter.road_segment_id : undefined,
            road_names: Array.isArray(roadAggregateFilter.road_names)
              ? roadAggregateFilter.road_names.filter((v: any) => typeof v === 'string' && v.trim().length > 0)
              : undefined,
            min_points: typeof roadAggregateFilter.min_points === 'number' ? roadAggregateFilter.min_points : undefined,
            limit: typeof roadAggregateFilter.limit === 'number' ? roadAggregateFilter.limit : undefined,
          }
        : null;
      const hasRoadAggregateFilter = Boolean(
        normalizedRoadAggregateFilter &&
          (
            normalizedRoadAggregateFilter.road_name ||
            normalizedRoadAggregateFilter.road_segment_id ||
            (normalizedRoadAggregateFilter.road_names && normalizedRoadAggregateFilter.road_names.length > 0)
          )
      );

      if (hasRoadAggregateFilter && onRoadAggregateFilter) {
        onRoadAggregateFilter(normalizedRoadAggregateFilter!);
        actionTaken = true;
      }

      const shouldApplyPointSelection = hasServerPoints && (!hasRoadAggregateFilter || mapSelection?.overlay === true);
      if (shouldApplyPointSelection) {
        try {
          const selectionPoints = mapServerSelectionPoints(mapSelectionPoints);

          const nonzero = selectionPoints.find(
            (row) => Math.abs(row.acceleration?.x ?? 0) > 0.0001 || Math.abs(row.acceleration?.y ?? 0) > 0.0001
          );
          const sample = nonzero ?? selectionPoints[0];
          if (sample) {
            console.debug('[ChatMapSelection] accel sample', {
              total: selectionPoints.length,
              sampleId: sample.id,
              accX: sample.acceleration?.x ?? null,
              accY: sample.acceleration?.y ?? null,
              speed: sample.speed,
              type: sample.type,
            });
          }

          const validPoints = selectionPoints.filter(
            (p) => Number.isFinite(p.longitude) && Number.isFinite(p.latitude)
          );

          if (validPoints.length === 0) {
            console.warn('[Map] Server selection had no valid coordinates', mapSelectionPoints.slice(0, 5));
          } else {
            onServerSelection?.(validPoints, mapSelection?.overlay);
            actionTaken = true;
          }
        } catch (selectionError) {
          console.error('Failed to process server selection payload:', selectionError);
        }
      }

      if (hasServerLines) {
        try {
          const mappedLines = mapServerWorkzoneLines(mapSelectionLines);
          // When both points and lines arrive together (e.g. combined/conflation maps),
          // mark lines as non-exclusive so they don't clear the point selection.
          if (shouldApplyPointSelection && mappedLines.length > 0) {
            for (const line of mappedLines) {
              line.exclusive = false;
            }
          }
          onWorkzoneLines?.(mappedLines);
          actionTaken = true;
        } catch (lineError) {
          console.error('Failed to process workzone lines:', lineError);
        }
      }

      if (chartPayloads.length > 0) {
        onChartPayload?.(chartPayloads);
      }

    } catch (error: any) {
      console.error('Chat error:', error);
      console.error('Error details:', error.message, error.stack);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Error connecting to server: ${error.message || 'Please check if the backend is running.'}`
      }]);
    } finally {
      setIsLoading(false);
    }
  }, [
    messages,
    uploadedFileData,
    onVisualize,
    onShowChart,
    onChartPayload,
    onServerSelection,
    onWorkzoneLines,
    onRoadAggregateFilter,
    onClearHistory,
    resetUploadState,
    chatStorageKey,
    setMessages,
    setIsLoading,
  ]);

  return {
    handleSend,
    handleCrashAnalyze,
    handleWorkzoneAnalyze,
  };
};

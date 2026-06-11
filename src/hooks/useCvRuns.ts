import { useState, useCallback, useEffect, useMemo } from 'react';
import { getCvRuns, setActiveCvRun, type CvRunSummary } from '../api/cvRunsClient';
import { resolveRoadTileUrl, pickRunsForSelector, buildRunOptionLabel } from '../features/cv-runs/runOptions';
import { ROAD_TILE_URL, ROAD_TILE_DATASET } from '../config';
import { withTimeout } from '../utils/async';
import type { ExternalChatMessage } from '../features/shared/types';

export interface UseCvRunsResult {
  cvRuns: CvRunSummary[];
  activeCvRunId: string;
  cvRunLoading: boolean;
  cvRunSwitching: boolean;
  cvRunError: string | null;
  roadTileUrlResolved: string | null;
  useRoadTiles: boolean;
  cvRunOptions: Array<CvRunSummary & { optionLabel: string }>;
  refreshCvRuns: () => Promise<void>;
  handleActiveCvRunChange: (nextRunId: string) => Promise<void>;
}

export const useCvRuns = (roadTileMode?: 'speed' | 'vehicles'): UseCvRunsResult => {
  const [cvRuns, setCvRuns] = useState<CvRunSummary[]>([]);
  const [cvRunLoading, setCvRunLoading] = useState(false);
  const [cvRunSwitching, setCvRunSwitching] = useState(false);
  const [cvRunError, setCvRunError] = useState<string | null>(null);
  const [activeCvRunId, setActiveCvRunId] = useState<string>(() => {
    const stored = sessionStorage.getItem('active_cv_run_id');
    if (stored && stored.trim()) return stored.trim();
    return ROAD_TILE_DATASET || '';
  });

  const roadTileDatasetId = (activeCvRunId || ROAD_TILE_DATASET || '').trim();
  const roadTileUrlResolved = useMemo(() => {
    if (!roadTileDatasetId) return null;
    return resolveRoadTileUrl(ROAD_TILE_URL, roadTileDatasetId, roadTileMode) || null;
  }, [roadTileDatasetId, roadTileMode]);
  const useRoadTiles = Boolean(roadTileUrlResolved && roadTileDatasetId);

  const refreshCvRuns = useCallback(async () => {
    setCvRunLoading(true);
    setCvRunError(null);
    try {
      const data = await getCvRuns();
      const allRuns = Array.isArray(data?.runs) ? data.runs : [];
      const activeFromList = allRuns.find((run) => run.is_active)?.run_id;
      const activeFromPayload = typeof data?.active_run_id === 'string' ? data.active_run_id : '';
      const resolvedActiveRun = (activeFromList || activeFromPayload || '').trim();
      const currentRunId = String(activeCvRunId || '').trim();
      const runs = pickRunsForSelector(allRuns, resolvedActiveRun || currentRunId);
      setCvRuns(runs);
      const nextActiveRunId = (() => {
        if (resolvedActiveRun) return resolvedActiveRun;
        if (!runs.length) return currentRunId;
        if (currentRunId && runs.some((run) => String(run.run_id || '').trim() === currentRunId)) {
          return currentRunId;
        }
        return String(runs[0]?.run_id || '').trim();
      })();
      if (nextActiveRunId !== currentRunId) {
        setActiveCvRunId(nextActiveRunId);
      }
    } catch (error: any) {
      setCvRunError(error?.message || 'Unable to load CV runs');
      setCvRuns([]);
    } finally {
      setCvRunLoading(false);
    }
  }, [activeCvRunId]);

  useEffect(() => {
    refreshCvRuns();
  }, [refreshCvRuns]);

  useEffect(() => {
    if (!activeCvRunId) {
      sessionStorage.removeItem('active_cv_run_id');
      return;
    }
    sessionStorage.setItem('active_cv_run_id', activeCvRunId);
  }, [activeCvRunId]);

  const cvRunOptions = useMemo(() => {
    const base = cvRuns.map((run) => ({
      ...run,
      optionLabel: buildRunOptionLabel(run),
    }));

    const labelCounts = new globalThis.Map<string, number>();
    base.forEach((run) => {
      labelCounts.set(run.optionLabel, (labelCounts.get(run.optionLabel) || 0) + 1);
    });

    return base.map((run) => {
      if ((labelCounts.get(run.optionLabel) || 0) <= 1) return run;
      const suffix = String(run.schema_name || run.run_id || '').trim();
      return {
        ...run,
        optionLabel: suffix ? `${run.optionLabel} • ${suffix}` : run.optionLabel,
      };
    });
  }, [cvRuns]);

  const handleActiveCvRunChange = useCallback(async (
    nextRunId: string,
    options?: {
      onClearData?: () => void;
      onClearWorkzones?: () => void;
      onClearArea?: () => void;
      onResetFilter?: () => void;
      onResetHistory?: () => void;
      onSetOverride?: (override: boolean) => void;
      onSetLayerMode?: (mode: 'road-network' | 'focus-selection' | 'mixed') => void;
      onLoadRoadAggregates?: () => Promise<void>;
      onResetMapBounds?: () => Promise<void>;
      onShowWarning?: (message: ExternalChatMessage) => void;
    }
  ) => {
    const runId = (nextRunId || '').trim();
    if (!runId || runId === activeCvRunId || cvRunSwitching) {
      return;
    }

    setCvRunSwitching(true);
    setCvRunError(null);
    try {
      const response = await withTimeout(setActiveCvRun(runId), 20000, 'CV run switch request');
      setActiveCvRunId(runId);
      await withTimeout(refreshCvRuns(), 12000, 'CV run refresh');

      options?.onClearData?.();
      options?.onClearWorkzones?.();
      options?.onClearArea?.();
      options?.onResetFilter?.();
      options?.onResetHistory?.();
      options?.onSetOverride?.(true);
      options?.onSetLayerMode?.('road-network');

      if (Array.isArray(response?.warnings) && response.warnings.length > 0) {
        options?.onShowWarning?.({
          id: `warning-${Date.now()}`,
          role: 'assistant',
          content: response.warnings.join(' '),
        });
      }

      // Don't keep the run switch UI locked while map refresh/bounds queries complete.
      void (async () => {
        try {
          if (!useRoadTiles) {
            await options?.onLoadRoadAggregates?.();
          }
          await options?.onResetMapBounds?.();
        } catch (postSwitchError: any) {
          console.debug('[CV Run] post-switch map refresh warning:', postSwitchError);
        }
      })();
    } catch (error: any) {
      const message = String(error?.message || '');
      if (message.toLowerCase().includes('timed out')) {
        setCvRunError(`${message}. The backend may be busy; try again in a few seconds.`);
      } else {
        setCvRunError(message || 'Failed to switch CV run');
      }
    } finally {
      setCvRunSwitching(false);
    }
  }, [activeCvRunId, cvRunSwitching, refreshCvRuns, useRoadTiles]);

  return {
    cvRuns,
    activeCvRunId,
    cvRunLoading,
    cvRunSwitching,
    cvRunError,
    roadTileUrlResolved,
    useRoadTiles,
    cvRunOptions,
    refreshCvRuns,
    handleActiveCvRunChange,
  };
};

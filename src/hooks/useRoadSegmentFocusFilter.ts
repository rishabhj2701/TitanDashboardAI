import { useEffect } from 'react';
import { sanitizeRoadFilterTerms } from '../features/map/roadFilterTerms';

export interface UseRoadSegmentFocusFilterParams {
  mapReady: boolean;
  layerBehaviorMode: string;
  mapOverrideActive: boolean;
  roadAggregateFilter: any;
  mapDataset: any[];
  vehicleData: any[];
  workzoneLines: any[];
  getMapDataForRender: () => any[];
  applyRoadSegmentFocusFilter: (segmentIds: string[] | null, roadNames: string[] | null) => void;
}

export const useRoadSegmentFocusFilter = (params: UseRoadSegmentFocusFilterParams) => {
  const {
    mapReady,
    layerBehaviorMode,
    mapOverrideActive,
    roadAggregateFilter,
    mapDataset,
    vehicleData,
    workzoneLines,
    getMapDataForRender,
    applyRoadSegmentFocusFilter,
  } = params;

  useEffect(() => {
    if (!mapReady) return;

    if (layerBehaviorMode === 'focus-selection') {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    const hasExclusiveWorkzones =
      workzoneLines.length > 0 && workzoneLines.every((line) => line.exclusive !== false);
    if (hasExclusiveWorkzones) {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    if (roadAggregateFilter) {
      const scopedRoadNames = sanitizeRoadFilterTerms([
        ...(roadAggregateFilter.road_names || []),
        ...(roadAggregateFilter.road_name ? [roadAggregateFilter.road_name] : []),
      ]);
      const scopedSegmentIds = roadAggregateFilter.road_segment_id
        ? [roadAggregateFilter.road_segment_id]
        : null;
      applyRoadSegmentFocusFilter(scopedSegmentIds, scopedRoadNames.length ? scopedRoadNames : null);
      return;
    }

    if (!mapOverrideActive) {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    const dataForMap = getMapDataForRender();
    if (!dataForMap.length) {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    const isCvOnlySelection = dataForMap.every((point) => {
      const t = String(point.type ?? '').toLowerCase();
      return t !== 'crash' && t !== 'workzone';
    });
    if (!isCvOnlySelection) {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    const roadSegmentIds = Array.from(
      new Set(
        dataForMap
          .map((point) => point.road_segment_id ?? (point as any).roadSegmentId ?? null)
          .filter((id): id is string | number => id !== null && id !== undefined && String(id).trim() !== '')
          .map((id) => String(id))
      )
    );

    if (!roadSegmentIds.length) {
      applyRoadSegmentFocusFilter(null, null);
      return;
    }

    applyRoadSegmentFocusFilter(roadSegmentIds, null);
  }, [
    mapReady,
    layerBehaviorMode,
    mapOverrideActive,
    roadAggregateFilter,
    mapDataset,
    vehicleData,
    workzoneLines,
    getMapDataForRender,
    applyRoadSegmentFocusFilter,
  ]);
};

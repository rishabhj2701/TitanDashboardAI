import { useCallback } from 'react';
import type { ProcessedVehicleData } from '../services/dataLoader';
import type { RoadAggregateFilter, WorkzoneLine } from '../features/map/types';
import { loadCvRoadAggregates } from '../services/dataLoader';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';

export interface UseAppHandlersParams {
  // State
  vehicleData: ProcessedVehicleData[];
  mapDataset: ProcessedVehicleData[];
  filteredData: ProcessedVehicleData[];
  workzoneLines: WorkzoneLine[];
  mapHistory: any;
  mapPrevious: ProcessedVehicleData[] | null;
  mapOverrideActive: boolean;
  layerBehaviorMode: 'road-network' | 'focus-selection' | 'mixed';
  roadAggregateFilter: RoadAggregateFilter | null;
  areaPolygon: GeoJSON.Polygon | null;
  cvRoadGeojson: CvRoadAggregateGeoJSON | null;
  useRoadTiles: boolean;
  roadTileDatasetId: string;
  
  // Setters
  setVehicleData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapDataset: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setFilteredData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapPrevious: React.Dispatch<React.SetStateAction<ProcessedVehicleData[] | null>>;
  setMapOverrideActive: React.Dispatch<React.SetStateAction<boolean>>;
  setLayerBehaviorMode: React.Dispatch<React.SetStateAction<'road-network' | 'focus-selection' | 'mixed'>>;
  setRoadAggregateFilter: React.Dispatch<React.SetStateAction<RoadAggregateFilter | null>>;
  setMapHistory: React.Dispatch<React.SetStateAction<any>>;
  setWorkzoneLines: React.Dispatch<React.SetStateAction<WorkzoneLine[]>>;
  setCvRoadGeojson: React.Dispatch<React.SetStateAction<CvRoadAggregateGeoJSON | null>>;
  setCvRoadLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setAreaPolygon: React.Dispatch<React.SetStateAction<GeoJSON.Polygon | null>>;
  
  // Functions
  captureMapHistory: () => void;
  clearAreaOverlay: () => void;
  applySelectionLayerBehavior: (opts?: { overlay?: boolean; polygon?: boolean }) => void;
  resetMapToRoadNetwork: () => Promise<void>;
}

export interface UseAppHandlersResult {
  handleFilter: (filterType: string, value: any) => void;
  handleVisualize: (type: string) => void;
  handleServerSelection: (points: ProcessedVehicleData[], overlay?: boolean) => void;
  handleResetMap: () => Promise<void>;
  handleClearHistoryView: () => Promise<void>;
  handleWorkzoneSelection: (lines: WorkzoneLine[]) => void;
  handleUndoMap: () => void;
  handleRoadAggregateFilter: (filter: RoadAggregateFilter) => Promise<void>;
}

const coordKey = (value: unknown): string => {
  const num = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(num) ? num.toFixed(6) : 'na';
};

const buildPointIdentity = (point: ProcessedVehicleData): string => {
  const primaryId = point.primary_id || point.hp_acc_image_no || point.id || '';
  const crashDate = point.accident_date || '';
  const crashTime = point.accident_time || '';
  const timestamp = point.timestamp || '';
  const type = (point.type || '').toLowerCase();
  return [
    type,
    primaryId,
    crashDate,
    crashTime,
    timestamp,
    coordKey(point.latitude),
    coordKey(point.longitude),
  ].join('|');
};

const dedupePoints = (points: ProcessedVehicleData[]): ProcessedVehicleData[] => {
  const seen = new Set<string>();
  const deduped: ProcessedVehicleData[] = [];
  for (const point of points) {
    const key = buildPointIdentity(point);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(point);
  }
  return deduped;
};

export const useAppHandlers = (params: UseAppHandlersParams): UseAppHandlersResult => {
  const {
    vehicleData,
    mapDataset,
    filteredData,
    workzoneLines,
    mapHistory,
    mapPrevious,
    cvRoadGeojson,
    useRoadTiles,
    setVehicleData,
    setMapDataset,
    setFilteredData,
    setMapPrevious,
    setMapOverrideActive,
    setLayerBehaviorMode,
    setRoadAggregateFilter,
    setMapHistory,
    setWorkzoneLines,
    setCvRoadGeojson,
    setCvRoadLoading,
    setAreaPolygon,
    captureMapHistory,
    clearAreaOverlay,
    applySelectionLayerBehavior,
    resetMapToRoadNetwork,
  } = params;

  const handleFilter = useCallback((filterType: string, value: any) => {
    if (!vehicleData.length) return;

    let filtered = vehicleData;

    if (filterType === 'speeding') {
      filtered = vehicleData.filter(v => v.speed > v.speedLimit);
    } else if (filterType === 'road') {
      filtered = vehicleData.filter(v =>
        v.roadName && v.roadName.toLowerCase().includes(value.toLowerCase())
      );
    }

    captureMapHistory();
    clearAreaOverlay();
    setMapPrevious(mapDataset);
    setFilteredData(filtered);
    setMapDataset(filtered.length ? filtered : vehicleData);
    setMapOverrideActive(false);
    setRoadAggregateFilter(null);
    setLayerBehaviorMode('focus-selection');
  }, [vehicleData, mapDataset, captureMapHistory, clearAreaOverlay, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setRoadAggregateFilter, setLayerBehaviorMode]);

  const handleVisualize = useCallback((_type: string) => {
    if (_type === 'all' || filteredData.length === 0) {
      captureMapHistory();
      clearAreaOverlay();
      setMapPrevious(mapDataset);
      setFilteredData([]);
      setMapDataset(vehicleData);
      setMapOverrideActive(false);
      setRoadAggregateFilter(null);
      setLayerBehaviorMode(vehicleData.length > 0 ? 'focus-selection' : 'road-network');
    } else {
      captureMapHistory();
      clearAreaOverlay();
      setMapPrevious(mapDataset);
      setMapDataset(filteredData);
      setMapOverrideActive(true);
      setRoadAggregateFilter(null);
      setLayerBehaviorMode('focus-selection');
    }
  }, [filteredData, vehicleData, mapDataset, captureMapHistory, clearAreaOverlay, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setRoadAggregateFilter, setLayerBehaviorMode]);

  const handleServerSelection = useCallback((points: ProcessedVehicleData[], overlay?: boolean) => {
    if (!points) return;
    setRoadAggregateFilter(null);
    if (points.length === 0) {
      captureMapHistory();
      setMapPrevious(mapDataset);
      setFilteredData([]);
      setMapDataset([]);
      setMapOverrideActive(true);
      setLayerBehaviorMode('road-network');
      return;
    }

    console.log(`[Map] handleServerSelection received ${points.length} points (overlay=${!!overlay})`);
    if (workzoneLines.length > 0 && workzoneLines.every((line) => line.exclusive !== false)) {
      setWorkzoneLines([]);
    }
    clearAreaOverlay();
    captureMapHistory();
    if (overlay) {
      setMapDataset(prev => {
        setMapPrevious(prev);
        return dedupePoints([...prev, ...points]);
      });
      setFilteredData(prev => dedupePoints([...prev, ...points]));
      setMapOverrideActive(true);
    } else {
      setMapPrevious(mapDataset);
      const deduped = dedupePoints(points);
      setFilteredData(deduped);
      setMapDataset(deduped);
      setMapOverrideActive(true);
    }
    applySelectionLayerBehavior({ overlay: !!overlay });
  }, [mapDataset, workzoneLines, captureMapHistory, clearAreaOverlay, applySelectionLayerBehavior, setRoadAggregateFilter, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setLayerBehaviorMode, setWorkzoneLines]);

  const handleResetMap = useCallback(async () => {
    captureMapHistory();
    clearAreaOverlay();
    setRoadAggregateFilter(null);

    if (!useRoadTiles && !cvRoadGeojson) {
      try {
        setCvRoadLoading(true);
        const roadAgg = await loadCvRoadAggregates();
        setCvRoadGeojson(roadAgg);
      } catch (error) {
        console.error('Failed to reload CV road aggregates for reset:', error);
      } finally {
        setCvRoadLoading(false);
      }
    }

    setMapPrevious(mapDataset);
    setFilteredData([]);
    setMapDataset([]);
    setMapOverrideActive(true);
    setLayerBehaviorMode('road-network');
    if (workzoneLines.length > 0) {
      setWorkzoneLines([]);
    }
    await resetMapToRoadNetwork();
  }, [useRoadTiles, cvRoadGeojson, mapDataset, workzoneLines, captureMapHistory, clearAreaOverlay, resetMapToRoadNetwork, setRoadAggregateFilter, setCvRoadLoading, setCvRoadGeojson, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setLayerBehaviorMode, setWorkzoneLines]);

  const handleClearHistoryView = useCallback(async () => {
    clearAreaOverlay();
    setRoadAggregateFilter(null);
    setMapHistory(null);
    setMapPrevious(null);
    setFilteredData([]);
    setMapDataset([]);
    setMapOverrideActive(true);
    setLayerBehaviorMode('road-network');
    if (workzoneLines.length > 0) {
      setWorkzoneLines([]);
    }
    await resetMapToRoadNetwork();
  }, [workzoneLines, clearAreaOverlay, resetMapToRoadNetwork, setRoadAggregateFilter, setMapHistory, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setLayerBehaviorMode, setWorkzoneLines]);

  const handleWorkzoneSelection = useCallback((lines: WorkzoneLine[]) => {
    captureMapHistory();
    clearAreaOverlay();
    setRoadAggregateFilter(null);
    const isExclusive = lines.length > 0 && lines.every((line) => line.exclusive !== false);
    if (isExclusive) {
      setMapPrevious(mapDataset);
      setFilteredData([]);
      setMapDataset([]);
      setMapOverrideActive(true);
      setLayerBehaviorMode('focus-selection');
    } else {
      setLayerBehaviorMode('mixed');
    }
    setWorkzoneLines(lines);
  }, [mapDataset, captureMapHistory, clearAreaOverlay, setRoadAggregateFilter, setMapPrevious, setFilteredData, setMapDataset, setMapOverrideActive, setLayerBehaviorMode, setWorkzoneLines]);

  const handleUndoMap = useCallback(() => {
    setRoadAggregateFilter(null);
    if (mapHistory) {
      setMapDataset(mapHistory.points);
      setFilteredData(mapHistory.filtered);
      setWorkzoneLines(mapHistory.workzoneLines);
      setMapOverrideActive(mapHistory.override);
      setLayerBehaviorMode(mapHistory.layerMode ?? 'road-network');
      setAreaPolygon(mapHistory.areaPolygon);
      setMapHistory(null);
      return;
    }
    if (!mapPrevious || mapPrevious.length === 0) return;
    if (workzoneLines.length > 0) {
      setWorkzoneLines([]);
    }
    setFilteredData(mapPrevious);
    setMapDataset(mapPrevious);
    setMapOverrideActive(true);
    setLayerBehaviorMode('focus-selection');
    setMapPrevious(null);
  }, [mapPrevious, workzoneLines, mapHistory, setRoadAggregateFilter, setMapDataset, setFilteredData, setWorkzoneLines, setMapOverrideActive, setLayerBehaviorMode, setAreaPolygon, setMapHistory, setMapPrevious]);

  const handleRoadAggregateFilter = useCallback(async (filter: RoadAggregateFilter) => {
    captureMapHistory();
    clearAreaOverlay();
    const roadName = typeof filter.road_name === 'string' ? filter.road_name.trim() : '';
    const roadSegmentId = typeof filter.road_segment_id === 'string' ? filter.road_segment_id.trim() : '';
    const roadNames = Array.from(new Set(
      (Array.isArray(filter.road_names) ? filter.road_names : [])
        .map((name) => String(name).trim())
        .filter(Boolean)
    ));
    const normalizedFilter: RoadAggregateFilter = {
      road_name: roadName || undefined,
      road_segment_id: roadSegmentId || undefined,
      road_names: roadNames.length ? roadNames : undefined,
      min_points: filter.min_points ?? undefined,
      limit: filter.limit ?? undefined,
    };
    const nextRoadAggregateFilter = (
      normalizedFilter.road_name || normalizedFilter.road_segment_id || (normalizedFilter.road_names && normalizedFilter.road_names.length > 0)
        ? normalizedFilter
        : null
    );

    if (useRoadTiles) {
      setRoadAggregateFilter(nextRoadAggregateFilter);
      setVehicleData([]);
      setMapDataset([]);
      setFilteredData([]);
      setMapOverrideActive(true);
      setLayerBehaviorMode('road-network');
      if (workzoneLines.length > 0) {
        setWorkzoneLines([]);
      }
      setCvRoadGeojson(null);
      return;
    }

    setCvRoadLoading(true);
    try {
      const roadAgg = await loadCvRoadAggregates(normalizedFilter);
      setRoadAggregateFilter(nextRoadAggregateFilter);
      setVehicleData([]);
      setMapDataset([]);
      setFilteredData([]);
      setMapOverrideActive(true);
      setLayerBehaviorMode('road-network');
      if (workzoneLines.length > 0) {
        setWorkzoneLines([]);
      }
      setCvRoadGeojson(roadAgg);
    } catch (error) {
      console.error('Failed to load CV road aggregates for filter:', error);
    } finally {
      setCvRoadLoading(false);
    }
  }, [captureMapHistory, workzoneLines, clearAreaOverlay, useRoadTiles, setRoadAggregateFilter, setVehicleData, setMapDataset, setFilteredData, setMapOverrideActive, setLayerBehaviorMode, setWorkzoneLines, setCvRoadGeojson, setCvRoadLoading]);

  return {
    handleFilter,
    handleVisualize,
    handleServerSelection,
    handleResetMap,
    handleClearHistoryView,
    handleWorkzoneSelection,
    handleUndoMap,
    handleRoadAggregateFilter,
  };
};

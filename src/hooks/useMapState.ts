import { useState, useCallback, useMemo } from 'react';
import type { ProcessedVehicleData } from '../services/dataLoader';
import type { LayerBehaviorMode, RoadAggregateFilter, WorkzoneLine } from '../features/map/types';

export interface MapHistory {
  points: ProcessedVehicleData[];
  workzoneLines: WorkzoneLine[];
  override: boolean;
  layerMode: LayerBehaviorMode;
  filtered: ProcessedVehicleData[];
  areaPolygon: GeoJSON.Polygon | null;
}

export interface MapStats {
  total: number;
  cvPoints: number;
  crashes: number;
  hardBraking: number;
  avgSpeed: number;
  maxSpeed: number;
}

export interface UseMapStateResult {
  // State
  vehicleData: ProcessedVehicleData[];
  mapDataset: ProcessedVehicleData[];
  filteredData: ProcessedVehicleData[];
  mapPrevious: ProcessedVehicleData[] | null;
  mapOverrideActive: boolean;
  layerBehaviorMode: LayerBehaviorMode;
  roadAggregateFilter: RoadAggregateFilter | null;
  mapHistory: MapHistory | null;
  workzoneLines: WorkzoneLine[];
  
  // Computed
  mapStats: MapStats;
  getMapDataForRender: () => ProcessedVehicleData[];
  
  // Actions
  setVehicleData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapDataset: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setFilteredData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapPrevious: React.Dispatch<React.SetStateAction<ProcessedVehicleData[] | null>>;
  setMapOverrideActive: React.Dispatch<React.SetStateAction<boolean>>;
  setLayerBehaviorMode: React.Dispatch<React.SetStateAction<LayerBehaviorMode>>;
  setRoadAggregateFilter: React.Dispatch<React.SetStateAction<RoadAggregateFilter | null>>;
  setMapHistory: React.Dispatch<React.SetStateAction<MapHistory | null>>;
  setWorkzoneLines: React.Dispatch<React.SetStateAction<WorkzoneLine[]>>;
  
  // Functions
  captureMapHistory: (areaPolygon?: GeoJSON.Polygon | null) => void;
  applySelectionLayerBehavior: (opts?: { overlay?: boolean; polygon?: boolean }) => void;
}

export const useMapState = (): UseMapStateResult => {
  const [vehicleData, setVehicleData] = useState<ProcessedVehicleData[]>([]);
  const [mapDataset, setMapDataset] = useState<ProcessedVehicleData[]>([]);
  const [mapPrevious, setMapPrevious] = useState<ProcessedVehicleData[] | null>(null);
  const [filteredData, setFilteredData] = useState<ProcessedVehicleData[]>([]);
  const [mapOverrideActive, setMapOverrideActive] = useState(false);
  const [layerBehaviorMode, setLayerBehaviorMode] = useState<LayerBehaviorMode>('road-network');
  const [roadAggregateFilter, setRoadAggregateFilter] = useState<RoadAggregateFilter | null>(null);
  const [mapHistory, setMapHistory] = useState<MapHistory | null>(null);
  const [workzoneLines, setWorkzoneLines] = useState<WorkzoneLine[]>([]);

  const captureMapHistory = useCallback((areaPolygon?: GeoJSON.Polygon | null) => {
    setMapHistory({
      points: [...mapDataset],
      workzoneLines: [...workzoneLines],
      override: mapOverrideActive,
      layerMode: layerBehaviorMode,
      filtered: [...filteredData],
      areaPolygon: areaPolygon ?? null,
    });
  }, [mapDataset, workzoneLines, mapOverrideActive, layerBehaviorMode, filteredData]);

  const applySelectionLayerBehavior = useCallback((opts?: { overlay?: boolean; polygon?: boolean }) => {
    if (opts?.polygon) {
      setLayerBehaviorMode('focus-selection');
      return;
    }
    setLayerBehaviorMode(opts?.overlay ? 'mixed' : 'focus-selection');
  }, []);

  const getMapDataForRender = useCallback(() => {
    if (mapOverrideActive) {
      return mapDataset;
    }
    if (mapDataset.length > 0) {
      return mapDataset;
    }
    return vehicleData;
  }, [mapOverrideActive, mapDataset, vehicleData]);

  // Compute statistics from map data
  const mapStats = useMemo(() => {
    const data = getMapDataForRender();
    if (data.length === 0) {
      return { total: 0, cvPoints: 0, crashes: 0, hardBraking: 0, avgSpeed: 0, maxSpeed: 0 };
    }

    let cvPoints = 0;
    let crashes = 0;
    let hardBraking = 0;
    let totalSpeed = 0;
    let speedCount = 0;
    let maxSpeed = 0;

    data.forEach(d => {
      const type = (d.type ?? '').toLowerCase();
      if (type === 'crash') {
        crashes++;
      } else if (type === 'hardbrake') {
        hardBraking++;
      } else {
        cvPoints++;
      }

      if (d.speed != null && d.speed > 0) {
        totalSpeed += d.speed;
        speedCount++;
        if (d.speed > maxSpeed) maxSpeed = d.speed;
      }
    });

    return {
      total: data.length,
      cvPoints,
      crashes,
      hardBraking,
      avgSpeed: speedCount > 0 ? Math.round(totalSpeed / speedCount) : 0,
      maxSpeed: Math.round(maxSpeed),
    };
  }, [getMapDataForRender]);

  return {
    // State
    vehicleData,
    mapDataset,
    filteredData,
    mapPrevious,
    mapOverrideActive,
    layerBehaviorMode,
    roadAggregateFilter,
    mapHistory,
    workzoneLines,
    
    // Computed
    mapStats,
    getMapDataForRender,
    
    // Actions
    setVehicleData,
    setMapDataset,
    setFilteredData,
    setMapPrevious,
    setMapOverrideActive,
    setLayerBehaviorMode,
    setRoadAggregateFilter,
    setMapHistory,
    setWorkzoneLines,
    
    // Functions
    captureMapHistory,
    applySelectionLayerBehavior,
  };
};

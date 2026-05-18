import { useEffect } from 'react';
import { loadCvRoadAggregates } from '../services/dataLoader';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';
import { clearChat } from '../api/chatClient';
import { getSessionId } from '../api/session';
import { loadAppState } from '../utils/stateManager';
import type { ProcessedVehicleData } from '../services/dataLoader';
import type { WorkzoneLine } from '../features/map/types';

export interface UseMapInitializationParams {
  useRoadTiles: boolean;
  setCvRoadGeojson: React.Dispatch<React.SetStateAction<CvRoadAggregateGeoJSON | null>>;
  setCvRoadLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setVehicleData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapDataset: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setFilteredData: React.Dispatch<React.SetStateAction<ProcessedVehicleData[]>>;
  setMapOverrideActive: React.Dispatch<React.SetStateAction<boolean>>;
  setLayerBehaviorMode: React.Dispatch<React.SetStateAction<'road-network' | 'focus-selection' | 'mixed'>>;
  setWorkzoneLines: React.Dispatch<React.SetStateAction<WorkzoneLine[]>>;
  setChartSpecs: React.Dispatch<React.SetStateAction<any[]>>;
}

export const useMapInitialization = (params: UseMapInitializationParams) => {
  const {
    useRoadTiles,
    setCvRoadGeojson,
    setCvRoadLoading,
    setVehicleData,
    setMapDataset,
    setFilteredData,
    setMapOverrideActive,
    setLayerBehaviorMode,
    setWorkzoneLines,
    setChartSpecs,
  } = params;

  useEffect(() => {
    let isMounted = true;

    // Clean up old storage keys (one-time migration)
    sessionStorage.removeItem('traffic_app_state');
    localStorage.removeItem('traffic_app_state');

    // Clear backend session on page load
    const clearBackendSession = async () => {
      try {
        await clearChat({ sessionId: getSessionId() });
      } catch (error) {
        console.error('Failed to clear backend session:', error);
      }
    };

    const loadData = async () => {
      try {
        // Clear backend session first
        await clearBackendSession();

        // Load saved state
        const savedState = loadAppState();

        let roadAgg: CvRoadAggregateGeoJSON | null = null;
        if (!useRoadTiles) {
          try {
            setCvRoadLoading(true);
            roadAgg = await loadCvRoadAggregates();
            if (isMounted) {
              setCvRoadGeojson(roadAgg);
              setVehicleData([]);
              setMapDataset([]);
              setFilteredData([]);
              setMapOverrideActive(true);
              setLayerBehaviorMode('road-network');
            }
          } catch (error) {
            console.error('Failed to load CV road aggregates:', error);
          } finally {
            if (isMounted) {
              setCvRoadLoading(false);
            }
          }
        } else if (isMounted) {
          setCvRoadGeojson(null);
          setVehicleData([]);
          setMapDataset([]);
          setFilteredData([]);
          setMapOverrideActive(true);
          setLayerBehaviorMode('road-network');
        }

        if (!roadAgg && !useRoadTiles) {
          // Roads-only initial load. Do not auto-load points unless user already had a saved map dataset.
          const savedPoints = savedState?.mapState?.mapDataset;
          if (Array.isArray(savedPoints) && savedPoints.length > 0) {
            setVehicleData(savedPoints);
            setMapDataset(savedPoints);
          } else {
            setVehicleData([]);
            setMapDataset([]);
          }
        }

        // Restore workzone lines if available
        if (savedState?.mapState?.workzoneLines && savedState.mapState.workzoneLines.length > 0) {
          setWorkzoneLines(savedState.mapState.workzoneLines);
        }

        // Restore saved charts if available
        if (savedState?.generatedCharts && savedState.generatedCharts.length > 0) {
          setChartSpecs(savedState.generatedCharts);
        }
      } catch (error) {
        console.error('Failed to load vehicle data:', error);
      }
    };

    loadData();

    return () => {
      isMounted = false;
    };
  }, [
    useRoadTiles,
    setCvRoadGeojson,
    setCvRoadLoading,
    setVehicleData,
    setMapDataset,
    setFilteredData,
    setMapOverrideActive,
    setLayerBehaviorMode,
    setWorkzoneLines,
    setChartSpecs,
  ]);
};

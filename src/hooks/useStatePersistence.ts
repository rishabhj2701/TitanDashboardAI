import { useEffect } from 'react';
import { saveAppState } from '../utils/stateManager';
import type { ProcessedVehicleData } from '../services/dataLoader';
import type { WorkzoneLine } from '../features/map/types';

export interface UseStatePersistenceParams {
  chartSpecs: any[];
  workzoneLines: WorkzoneLine[];
  mapDataset: ProcessedVehicleData[];
}

export const useStatePersistence = (params: UseStatePersistenceParams) => {
  const { chartSpecs, workzoneLines, mapDataset } = params;

  useEffect(() => {
    // Only save if there's actually data to save
    if (chartSpecs.length > 0 || workzoneLines.length > 0) {
      saveAppState({
        generatedCharts: chartSpecs,
        vehicleData: [],
        selectedFilters: {},
        mapState: {
          center: [-90.2, 38.65],
          zoom: 11,
          mapDataset: mapDataset,
          workzoneLines: workzoneLines,
        },
        chatHistory: [],
        timestamp: Date.now(),
      });
    }
  }, [chartSpecs, workzoneLines, mapDataset]);
};

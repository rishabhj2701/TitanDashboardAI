import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAppHandlers, type UseAppHandlersParams } from './useAppHandlers';
import * as dataLoader from '../services/dataLoader';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';
import type { WorkzoneLine } from '../features/map/types';

vi.mock('../services/dataLoader', async () => {
  const actual = await vi.importActual<typeof import('../services/dataLoader')>('../services/dataLoader');
  return {
    ...actual,
    loadCvRoadAggregates: vi.fn(),
  };
});

const makeGeojson = (): CvRoadAggregateGeoJSON => ({
  type: 'FeatureCollection',
  features: [],
});

const makeWorkzone = (): WorkzoneLine => ({
  id: 'wz-1',
  coordinates: [[-90.0, 38.6], [-90.1, 38.7]],
  roadName: 'I-70',
});

const makeParams = (overrides: Partial<UseAppHandlersParams> = {}): UseAppHandlersParams => ({
  vehicleData: [],
  mapDataset: [],
  filteredData: [],
  workzoneLines: [],
  mapHistory: null,
  mapPrevious: null,
  mapOverrideActive: false,
  layerBehaviorMode: 'road-network',
  roadAggregateFilter: null,
  areaPolygon: null,
  cvRoadGeojson: null,
  useRoadTiles: false,
  roadTileDatasetId: '',
  setVehicleData: vi.fn(),
  setMapDataset: vi.fn(),
  setFilteredData: vi.fn(),
  setMapPrevious: vi.fn(),
  setMapOverrideActive: vi.fn(),
  setLayerBehaviorMode: vi.fn(),
  setRoadAggregateFilter: vi.fn(),
  setMapHistory: vi.fn(),
  setWorkzoneLines: vi.fn(),
  setCvRoadGeojson: vi.fn(),
  setCvRoadLoading: vi.fn(),
  setAreaPolygon: vi.fn(),
  captureMapHistory: vi.fn(),
  clearAreaOverlay: vi.fn(),
  applySelectionLayerBehavior: vi.fn(),
  resetMapToRoadNetwork: vi.fn().mockResolvedValue(undefined),
  ...overrides,
});

describe('useAppHandlers.handleRoadAggregateFilter', () => {
  const loadCvRoadAggregatesMock = vi.mocked(dataLoader.loadCvRoadAggregates);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not clear existing map state when non-tile aggregate load fails', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    loadCvRoadAggregatesMock.mockRejectedValueOnce(new Error('aggregate failed'));

    const params = makeParams();
    const { result } = renderHook(() => useAppHandlers(params));

    await act(async () => {
      await result.current.handleRoadAggregateFilter({ road_name: '%I 70%' });
    });

    expect(params.captureMapHistory).toHaveBeenCalledTimes(1);
    expect(params.clearAreaOverlay).toHaveBeenCalledTimes(1);
    expect(params.setCvRoadLoading).toHaveBeenNthCalledWith(1, true);
    expect(params.setCvRoadLoading).toHaveBeenNthCalledWith(2, false);
    expect(params.setRoadAggregateFilter).not.toHaveBeenCalled();
    expect(params.setVehicleData).not.toHaveBeenCalled();
    expect(params.setMapDataset).not.toHaveBeenCalled();
    expect(params.setFilteredData).not.toHaveBeenCalled();
    expect(params.setCvRoadGeojson).not.toHaveBeenCalled();

    consoleErrorSpy.mockRestore();
  });

  it('applies filter and clears overlays after non-tile aggregate load succeeds', async () => {
    const roadAgg = makeGeojson();
    loadCvRoadAggregatesMock.mockResolvedValueOnce(roadAgg);
    const params = makeParams({ workzoneLines: [makeWorkzone()] });
    const { result } = renderHook(() => useAppHandlers(params));

    await act(async () => {
      await result.current.handleRoadAggregateFilter({ road_name: ' I-70 ', road_names: ['I-70', ' I-70 '] });
    });

    expect(params.setRoadAggregateFilter).toHaveBeenCalledWith({
      road_name: 'I-70',
      road_names: ['I-70'],
      road_segment_id: undefined,
      min_points: undefined,
      limit: undefined,
    });
    expect(params.setVehicleData).toHaveBeenCalledWith([]);
    expect(params.setMapDataset).toHaveBeenCalledWith([]);
    expect(params.setFilteredData).toHaveBeenCalledWith([]);
    expect(params.setMapOverrideActive).toHaveBeenCalledWith(true);
    expect(params.setLayerBehaviorMode).toHaveBeenCalledWith('road-network');
    expect(params.setWorkzoneLines).toHaveBeenCalledWith([]);
    expect(params.setCvRoadGeojson).toHaveBeenCalledWith(roadAgg);
    expect(params.setCvRoadLoading).toHaveBeenNthCalledWith(1, true);
    expect(params.setCvRoadLoading).toHaveBeenNthCalledWith(2, false);
  });
});

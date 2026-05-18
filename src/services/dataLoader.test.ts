import { describe, expect, it, vi } from 'vitest';

import { loadCvRoadAggregates } from './dataLoader';
import * as cvClient from '../api/cvClient';

vi.mock('../api/session', () => ({
  getSessionId: vi.fn(() => 'sess-test'),
}));

vi.mock('../api/cvClient', async () => {
  const actual = await vi.importActual<typeof import('../api/cvClient')>('../api/cvClient');
  return {
    ...actual,
    aggregateCvRoads: vi.fn(),
  };
});

describe('loadCvRoadAggregates', () => {
  it('does not force source_table when none is specified', async () => {
    const aggregateCvRoadsMock = vi.mocked(cvClient.aggregateCvRoads);
    aggregateCvRoadsMock.mockResolvedValueOnce({
      status: 'success',
      geojson: { type: 'FeatureCollection', features: [] },
    });

    await loadCvRoadAggregates({ road_name: 'I-70', min_points: 1 });

    expect(aggregateCvRoadsMock).toHaveBeenCalledWith({
      dataset_id: undefined,
      source_table: undefined,
      min_points: 1,
      road_name: 'I-70',
      road_names: undefined,
      road_segment_id: undefined,
      limit: undefined,
    });
  });
});

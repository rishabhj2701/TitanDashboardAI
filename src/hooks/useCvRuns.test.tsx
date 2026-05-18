import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useCvRuns } from './useCvRuns';
import * as cvRunsClient from '../api/cvRunsClient';

vi.mock('../api/cvRunsClient', async () => {
  const actual = await vi.importActual<typeof import('../api/cvRunsClient')>('../api/cvRunsClient');
  return {
    ...actual,
    getCvRuns: vi.fn(),
    setActiveCvRun: vi.fn(),
  };
});

const createDeferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

describe('useCvRuns road tiles', () => {
  const getCvRunsMock = vi.mocked(cvRunsClient.getCvRuns);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps road tiles disabled until a dataset id is resolved', async () => {
    const deferred = createDeferred<cvRunsClient.CvRunsListResponsePayload>();
    getCvRunsMock.mockReturnValueOnce(deferred.promise);

    const { result } = renderHook(() => useCvRuns('speed'));

    expect(result.current.activeCvRunId).toBe('');
    expect(result.current.useRoadTiles).toBe(false);
    expect(result.current.roadTileUrlResolved).toBeNull();

    deferred.resolve({
      active_run_id: 'run-1',
      runs: [{ run_id: 'run-1', schema_name: 'cv_schema', is_active: true }],
    });

    await waitFor(() => {
      expect(result.current.activeCvRunId).toBe('run-1');
    });
    expect(result.current.useRoadTiles).toBe(true);
    expect(result.current.roadTileUrlResolved).toContain('dataset_id=run-1');
    expect(result.current.roadTileUrlResolved).toContain('mode=speed');
  });

  it('replaces a stale stored run id with the first available run', async () => {
    sessionStorage.setItem('active_cv_run_id', 'stale-run');
    getCvRunsMock.mockResolvedValue({
      runs: [{ run_id: 'run-expected', schema_name: 'cv_schema', created_at: '2025-01-01T00:00:00Z' }],
    });

    const { result } = renderHook(() => useCvRuns());

    await waitFor(() => {
      expect(result.current.activeCvRunId).toBe('run-expected');
    });
    expect(result.current.useRoadTiles).toBe(true);
    expect(result.current.roadTileUrlResolved).toContain('dataset_id=run-expected');
    expect(sessionStorage.getItem('active_cv_run_id')).toBe('run-expected');
  });
});

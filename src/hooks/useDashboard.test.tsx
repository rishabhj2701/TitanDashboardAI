import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useDashboard } from './useDashboard';

const STORE_BASE_KEY = 'titan_dashboards_v3';

describe('useDashboard user isolation', () => {
  it('loads dashboards from the active user-scoped key only', () => {
    localStorage.setItem(
      `${STORE_BASE_KEY}::alice`,
      JSON.stringify({
        dashboards: [{ id: 'dash-a', name: 'Alice Dashboard', widgets: [], createdAt: 1 }],
        activeDashboardId: 'dash-a',
      })
    );
    localStorage.setItem(
      `${STORE_BASE_KEY}::bob`,
      JSON.stringify({
        dashboards: [{ id: 'dash-b', name: 'Bob Dashboard', widgets: [], createdAt: 2 }],
        activeDashboardId: 'dash-b',
      })
    );

    const { result: aliceResult } = renderHook(() => useDashboard('alice'));
    expect(aliceResult.current.activeDashboardName).toBe('Alice Dashboard');

    const { result: bobResult } = renderHook(() => useDashboard('bob'));
    expect(bobResult.current.activeDashboardName).toBe('Bob Dashboard');
  });

  it('persists updates only to the current user-scoped key', () => {
    localStorage.setItem(
      `${STORE_BASE_KEY}::bob`,
      JSON.stringify({
        dashboards: [{ id: 'dash-b', name: 'Bob Dashboard', widgets: [], createdAt: 2 }],
        activeDashboardId: 'dash-b',
      })
    );

    const { result } = renderHook(() => useDashboard('alice'));
    act(() => {
      result.current.createDashboard('Alice Ops');
    });

    const aliceRaw = localStorage.getItem(`${STORE_BASE_KEY}::alice`);
    const bobRaw = localStorage.getItem(`${STORE_BASE_KEY}::bob`);
    expect(localStorage.getItem(STORE_BASE_KEY)).toBeNull();
    expect(aliceRaw).toContain('Alice Ops');
    expect(bobRaw).toContain('Bob Dashboard');
    expect(bobRaw).not.toContain('Alice Ops');
  });

  it('migrates legacy global dashboard store into the current user key one time', () => {
    localStorage.setItem(
      STORE_BASE_KEY,
      JSON.stringify({
        dashboards: [{ id: 'dash-legacy', name: 'Legacy Dashboard', widgets: [], createdAt: 3 }],
        activeDashboardId: 'dash-legacy',
      })
    );

    const { result } = renderHook(() => useDashboard('alice'));
    expect(result.current.activeDashboardName).toBe('Legacy Dashboard');

    const scoped = localStorage.getItem(`${STORE_BASE_KEY}::alice`) || '';
    expect(scoped).toContain('Legacy Dashboard');
    expect(localStorage.getItem(STORE_BASE_KEY)).toBeNull();
  });
});

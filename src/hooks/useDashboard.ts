import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  DashboardWidget, ChartWidget, RoadStatsWidget,
  RoadStatsData, WidgetLayout, WidgetType,
  Dashboard, DashboardStore, DashboardTemplate,
} from '../types/dashboard';
import {
  DEFAULT_WIDGET_LAYOUTS,
  ANALYTICS_WIDGET_TYPES,
  ANALYTICS_WIDGET_LABELS,
} from '../types/dashboard';
import type { GeneratedChartPayload } from '../types/charts';
import type { Layout } from 'react-grid-layout';
import { userScopedStorageKey } from '../utils/storageScope';

const STORE_BASE_KEY = 'titan_dashboards_v3';
const LEGACY_V2_KEY = 'titan_dashboard_v2';
const LEGACY_V1_KEY = 'titan_dashboard_v1';
const GRID_COLS = 12;

function uid(prefix = 'w') {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Smart layout: places widgets in a 2-col or 3-col grid filling gaps */
function smartLayout(types: WidgetType[], existingCount: number): DashboardWidget[] {
  let col = 0;
  let row = 0;
  // Calculate starting row based on existing widgets
  if (existingCount > 0) {
    row = Math.ceil(existingCount / 2) * 4; // approximate
  }

  return types.map((t) => {
    const defaults = DEFAULT_WIDGET_LAYOUTS[t] ?? DEFAULT_WIDGET_LAYOUTS['chart'];
    const w = defaults.w;
    // If this widget won't fit on the current row, move to next
    if (col + w > GRID_COLS) {
      col = 0;
      row += 4; // standard row height
    }
    const layout: WidgetLayout = { ...defaults, x: col, y: row };
    col += w;
    // If we've filled the row, move to next
    if (col >= GRID_COLS) {
      col = 0;
      row += 4;
    }
    return {
      id: uid(`widget-${t}`),
      type: t as any,
      title: ANALYTICS_WIDGET_LABELS[t] || t,
      addedAt: Date.now(),
      layout,
    };
  });
}

function ensureLayout(widget: DashboardWidget, index: number): DashboardWidget {
  if (widget.layout) return widget;
  const defaults: WidgetLayout = DEFAULT_WIDGET_LAYOUTS[widget.type] ?? DEFAULT_WIDGET_LAYOUTS['chart'];
  return {
    ...widget,
    layout: { ...defaults, x: (index * defaults.w) % 12, y: Infinity },
  };
}

function createEmptyDashboard(name: string): Dashboard {
  return { id: uid('dash'), name, widgets: [], createdAt: Date.now() };
}

function parseDashboardStore(raw: string | null): DashboardStore | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as DashboardStore;
    if (Array.isArray(parsed.dashboards) && parsed.dashboards.length > 0) {
      parsed.dashboards = parsed.dashboards.map(d => ({
        ...d,
        widgets: (d.widgets || []).map((w, i) => ensureLayout(w, i)),
      }));
      if (!parsed.activeDashboardId || !parsed.dashboards.find(d => d.id === parsed.activeDashboardId)) {
        parsed.activeDashboardId = parsed.dashboards[0].id;
      }
      return parsed;
    }
  } catch { /* ignore */ }
  return null;
}

function parseLegacyDashboardWidgets(raw: string | null): DashboardStore | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { widgets?: DashboardWidget[] };
    const widgets: DashboardWidget[] = Array.isArray(parsed.widgets)
      ? parsed.widgets.map((w: DashboardWidget, i: number) => ensureLayout(w, i))
      : [];
    const dash = createEmptyDashboard('My Dashboard');
    dash.widgets = widgets;
    return { dashboards: [dash], activeDashboardId: dash.id };
  } catch { /* ignore */ }
  return null;
}

function migrateLegacyStoreIfNeeded(storeKey: string): DashboardStore | null {
  const legacyKeys = [
    STORE_BASE_KEY,
    userScopedStorageKey(STORE_BASE_KEY, 'anonymous'),
    LEGACY_V2_KEY,
    LEGACY_V1_KEY,
  ];

  for (const legacyKey of legacyKeys) {
    if (legacyKey === storeKey) continue;
    const raw = localStorage.getItem(legacyKey);
    if (!raw) continue;

    const parsed = parseDashboardStore(raw) ?? parseLegacyDashboardWidgets(raw);
    if (!parsed) continue;

    localStorage.setItem(storeKey, JSON.stringify(parsed));
    localStorage.removeItem(legacyKey);
    return parsed;
  }
  return null;
}

function loadStore(storeKey: string): DashboardStore {
  const existing = parseDashboardStore(localStorage.getItem(storeKey));
  if (existing) return existing;

  const migrated = migrateLegacyStoreIfNeeded(storeKey);
  if (migrated) return migrated;

  const dash = createEmptyDashboard('My Dashboard');
  return { dashboards: [dash], activeDashboardId: dash.id };
}

function saveStore(store: DashboardStore, storeKey: string): void {
  try {
    localStorage.setItem(storeKey, JSON.stringify(store));
  } catch (err) {
    console.error('Failed to save dashboards:', err);
  }
}

export const useDashboard = (userId?: string | null) => {
  const storeKey = userScopedStorageKey(STORE_BASE_KEY, userId);
  const [store, setStore] = useState<DashboardStore>(() => loadStore(storeKey));
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [fullscreenWidgetId, setFullscreenWidgetId] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastRemovedRef = useRef<DashboardWidget | null>(null);

  useEffect(() => { saveStore(store, storeKey); }, [store, storeKey]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(() => setRefreshTick(t => t + 1), 30000);
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh]);

  const activeDashboard = store.dashboards.find(d => d.id === store.activeDashboardId) ?? store.dashboards[0];
  const widgets = activeDashboard?.widgets ?? [];

  const updateActiveWidgets = useCallback((updater: (prev: DashboardWidget[]) => DashboardWidget[]) => {
    setStore(prev => ({
      ...prev,
      dashboards: prev.dashboards.map(d =>
        d.id === prev.activeDashboardId
          ? { ...d, widgets: updater(d.widgets) }
          : d
      ),
    }));
  }, []);

  // --- Multi-dashboard ---
  const createDashboard = useCallback((name: string) => {
    const dash = createEmptyDashboard(name);
    setStore(prev => ({
      dashboards: [...prev.dashboards, dash],
      activeDashboardId: dash.id,
    }));
  }, []);

  const renameDashboard = useCallback((id: string, name: string) => {
    setStore(prev => ({
      ...prev,
      dashboards: prev.dashboards.map(d => d.id === id ? { ...d, name } : d),
    }));
  }, []);

  const deleteDashboard = useCallback((id: string) => {
    setStore(prev => {
      if (prev.dashboards.length <= 1) return prev;
      const filtered = prev.dashboards.filter(d => d.id !== id);
      const activeId = prev.activeDashboardId === id ? filtered[0].id : prev.activeDashboardId;
      return { dashboards: filtered, activeDashboardId: activeId };
    });
  }, []);

  const switchDashboard = useCallback((id: string) => {
    setStore(prev => ({ ...prev, activeDashboardId: id }));
  }, []);

  // --- Widget operations ---
  const removeWidget = useCallback((widgetId: string) => {
    updateActiveWidgets(prev => {
      const widget = prev.find(w => w.id === widgetId);
      if (widget) lastRemovedRef.current = widget;
      return prev.filter(w => w.id !== widgetId);
    });
  }, [updateActiveWidgets]);

  const undoRemoveWidget = useCallback(() => {
    const widget = lastRemovedRef.current;
    if (!widget) return false;
    updateActiveWidgets(prev => [...prev, widget]);
    lastRemovedRef.current = null;
    return true;
  }, [updateActiveWidgets]);

  const pinChart = useCallback((title: string, payload: GeneratedChartPayload) => {
    const widget: ChartWidget = {
      id: uid('widget'),
      type: 'chart',
      title,
      addedAt: Date.now(),
      chartPayload: payload,
      layout: { ...DEFAULT_WIDGET_LAYOUTS['chart'], x: 0, y: Infinity },
    };
    updateActiveWidgets(prev => [...prev, widget]);
  }, [updateActiveWidgets]);

  const pinRoadStats = useCallback((roadData: RoadStatsData) => {
    const widget: RoadStatsWidget = {
      id: uid('widget-road'),
      type: 'road-stats',
      title: `Road: ${roadData.road_name || 'Unknown'}`,
      addedAt: Date.now(),
      roadData,
      layout: { ...DEFAULT_WIDGET_LAYOUTS['road-stats'], x: 0, y: Infinity },
    };
    updateActiveWidgets(prev => [...prev, widget]);
  }, [updateActiveWidgets]);

  const updateLayouts = useCallback((layouts: Layout) => {
    updateActiveWidgets(prev => {
      const layoutMap = new Map(layouts.map(l => [l.i, l]));
      return prev.map(w => {
        const rgl = layoutMap.get(w.id);
        if (!rgl) return w;
        const next: WidgetLayout = {
          x: rgl.x, y: rgl.y, w: rgl.w, h: rgl.h,
          minW: w.layout?.minW, minH: w.layout?.minH,
        };
        if (
          w.layout &&
          w.layout.x === next.x && w.layout.y === next.y &&
          w.layout.w === next.w && w.layout.h === next.h
        ) return w;
        return { ...w, layout: next };
      });
    });
  }, [updateActiveWidgets]);

  const updateWidgetTitle = useCallback((widgetId: string, newTitle: string) => {
    updateActiveWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, title: newTitle } : w
    ));
  }, [updateActiveWidgets]);

  const toggleWidgetLock = useCallback((widgetId: string) => {
    updateActiveWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, locked: !w.locked } : w
    ));
  }, [updateActiveWidgets]);

  const addDataQualityWidget = useCallback(() => {
    updateActiveWidgets(prev => {
      if (prev.some(w => w.type === 'data-quality')) return prev;
      return [...prev, {
        id: uid('widget-dq'),
        type: 'data-quality' as const,
        title: 'Data Quality',
        addedAt: Date.now(),
        layout: { ...DEFAULT_WIDGET_LAYOUTS['data-quality'], x: 0, y: Infinity },
      }];
    });
  }, [updateActiveWidgets]);

  const addStatSummaryWidget = useCallback(() => {
    updateActiveWidgets(prev => {
      if (prev.some(w => w.type === 'stat-summary')) return prev;
      return [...prev, {
        id: uid('widget-stats'),
        type: 'stat-summary' as const,
        title: 'Dataset Summary',
        addedAt: Date.now(),
        layout: { ...DEFAULT_WIDGET_LAYOUTS['stat-summary'], x: 0, y: Infinity },
      }];
    });
  }, [updateActiveWidgets]);

  const addAnalyticsWidget = useCallback((type: WidgetType) => {
    if (!ANALYTICS_WIDGET_TYPES.includes(type)) return;
    updateActiveWidgets(prev => {
      if (prev.some(w => w.type === type)) return prev;
      return [...prev, {
        id: uid(`widget-${type}`),
        type: type as any,
        title: ANALYTICS_WIDGET_LABELS[type] || type,
        addedAt: Date.now(),
        layout: { ...DEFAULT_WIDGET_LAYOUTS[type], x: 0, y: Infinity },
      }];
    });
  }, [updateActiveWidgets]);

  /** Smart add: arranges widgets in a proper grid layout */
  const addAllAnalyticsWidgets = useCallback(() => {
    updateActiveWidgets(prev => {
      const existingTypes = new Set(prev.map(w => w.type));
      const newTypes = ANALYTICS_WIDGET_TYPES.filter(t => !existingTypes.has(t));
      if (newTypes.length === 0) return prev;
      const newWidgets = smartLayout(newTypes, prev.length);
      return [...prev, ...newWidgets];
    });
  }, [updateActiveWidgets]);

  /** Apply a template: clears current widgets and adds template widgets with smart layout */
  const applyTemplate = useCallback((template: DashboardTemplate) => {
    updateActiveWidgets(() => {
      return smartLayout(template.widgetTypes, 0);
    });
  }, [updateActiveWidgets]);

  const clearDashboard = useCallback(() => {
    updateActiveWidgets(() => []);
  }, [updateActiveWidgets]);

  const duplicateDashboard = useCallback((id: string) => {
    setStore(prev => {
      const source = prev.dashboards.find(d => d.id === id);
      if (!source) return prev;
      const newDash: Dashboard = {
        id: uid('dash'),
        name: `${source.name} (Copy)`,
        widgets: source.widgets.map(w => ({ ...w, id: uid('w') })),
        createdAt: Date.now(),
      };
      return {
        dashboards: [...prev.dashboards, newDash],
        activeDashboardId: newDash.id,
      };
    });
  }, []);

  const sortWidgetsByType = useCallback(() => {
    updateActiveWidgets(prev => {
      const sorted = [...prev].sort((a, b) => a.type.localeCompare(b.type));
      let col = 0;
      let row = 0;
      return sorted.map(w => {
        const defaults = DEFAULT_WIDGET_LAYOUTS[w.type] ?? DEFAULT_WIDGET_LAYOUTS['chart'];
        const ww = defaults.w;
        if (col + ww > GRID_COLS) { col = 0; row += 4; }
        const layout: WidgetLayout = { ...defaults, x: col, y: row };
        col += ww;
        if (col >= GRID_COLS) { col = 0; row += 4; }
        return { ...w, layout };
      });
    });
  }, [updateActiveWidgets]);

  const toggleWidgetCollapse = useCallback((widgetId: string) => {
    updateActiveWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, collapsed: !w.collapsed } : w
    ));
  }, [updateActiveWidgets]);

  const toggleAutoRefresh = useCallback(() => {
    setAutoRefresh(prev => !prev);
  }, []);

  // --- Export / Import ---
  const exportDashboard = useCallback(() => {
    const dash = store.dashboards.find(d => d.id === store.activeDashboardId);
    if (!dash) return;
    const blob = new Blob([JSON.stringify(dash, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = `${dash.name.replace(/\s+/g, '_')}.json`;
    link.href = url;
    link.click();
    URL.revokeObjectURL(url);
  }, [store]);

  const importDashboard = useCallback((json: string): boolean => {
    try {
      const parsed = JSON.parse(json);
      if (!parsed.name || !Array.isArray(parsed.widgets)) return false;
      const dash: Dashboard = {
        id: uid('dash'),
        name: parsed.name + ' (Imported)',
        widgets: (parsed.widgets as DashboardWidget[]).map((w, i) => ({
          ...ensureLayout(w as DashboardWidget, i),
          id: uid('w'),
        })),
        createdAt: Date.now(),
      };
      setStore(prev => ({
        dashboards: [...prev.dashboards, dash],
        activeDashboardId: dash.id,
      }));
      return true;
    } catch {
      return false;
    }
  }, []);

  // --- Color tag & Notes ---
  const setWidgetColorTag = useCallback((widgetId: string, color: string | undefined) => {
    updateActiveWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, colorTag: color } : w
    ));
  }, [updateActiveWidgets]);

  const setWidgetNote = useCallback((widgetId: string, note: string) => {
    updateActiveWidgets(prev => prev.map(w =>
      w.id === widgetId ? { ...w, note: note || undefined } : w
    ));
  }, [updateActiveWidgets]);

  // --- Resize presets ---
  const setWidgetSize = useCallback((widgetId: string, preset: 'small' | 'medium' | 'large') => {
    const sizes = { small: { w: 3, h: 3 }, medium: { w: 6, h: 4 }, large: { w: 12, h: 6 } };
    const { w, h } = sizes[preset];
    updateActiveWidgets(prev => prev.map(widget =>
      widget.id === widgetId
        ? { ...widget, layout: { ...widget.layout!, w, h } }
        : widget
    ));
  }, [updateActiveWidgets]);

  // --- Move widget between dashboards ---
  const moveWidgetToDashboard = useCallback((widgetId: string, targetDashboardId: string) => {
    setStore(prev => {
      const sourceId = prev.activeDashboardId;
      if (sourceId === targetDashboardId) return prev;
      const sourceDash = prev.dashboards.find(d => d.id === sourceId);
      if (!sourceDash) return prev;
      const widget = sourceDash.widgets.find(w => w.id === widgetId);
      if (!widget) return prev;
      return {
        ...prev,
        dashboards: prev.dashboards.map(d => {
          if (d.id === sourceId) return { ...d, widgets: d.widgets.filter(w => w.id !== widgetId) };
          if (d.id === targetDashboardId) return { ...d, widgets: [...d.widgets, { ...widget, layout: { ...widget.layout!, y: Infinity } }] };
          return d;
        }),
      };
    });
  }, []);

  return {
    dashboards: store.dashboards,
    activeDashboardId: store.activeDashboardId,
    activeDashboardName: activeDashboard?.name ?? 'Dashboard',
    createDashboard,
    renameDashboard,
    deleteDashboard,
    switchDashboard,
    widgets,
    removeWidget,
    pinChart,
    pinRoadStats,
    updateLayouts,
    updateWidgetTitle,
    toggleWidgetLock,
    addDataQualityWidget,
    addStatSummaryWidget,
    addAnalyticsWidget,
    addAllAnalyticsWidgets,
    applyTemplate,
    clearDashboard,
    duplicateDashboard,
    sortWidgetsByType,
    toggleWidgetCollapse,
    undoRemoveWidget,
    exportDashboard,
    importDashboard,
    setWidgetColorTag,
    setWidgetNote,
    setWidgetSize,
    moveWidgetToDashboard,
    autoRefresh,
    toggleAutoRefresh,
    refreshTick,
    fullscreenWidgetId,
    setFullscreenWidgetId,
  };
};

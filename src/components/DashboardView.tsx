import { useMemo, useCallback, useState, useEffect, useRef } from 'react';
import {
  ResponsiveGridLayout,
  useContainerWidth,
  verticalCompactor,
} from 'react-grid-layout';
import type { Layout, ResponsiveLayouts } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import type { DashboardWidget, WidgetType, Dashboard, DashboardTemplate } from '../types/dashboard';
import { ANALYTICS_WIDGET_TYPES, ANALYTICS_WIDGET_LABELS, DASHBOARD_TEMPLATES } from '../types/dashboard';
import DashboardWidgetCard, { WidgetBody } from './DashboardWidgetCard';
import './DashboardView.css';

const BREAKPOINTS = { lg: 1200, md: 900, sm: 600, xs: 0 };
const COLS = { lg: 12, md: 9, sm: 6, xs: 3 };
const ROW_HEIGHT = 80;

/* ---------- Toast System ---------- */
interface Toast { id: number; message: string; }

function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const addToast = useCallback((message: string) => {
    const id = nextId.current++;
    setToasts(prev => [...prev, { id, message }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
  }, []);

  return { toasts, addToast };
}

interface DashboardViewProps {
  widgets: DashboardWidget[];
  dashboards: Dashboard[];
  activeDashboardId: string;
  activeDashboardName: string;
  autoRefresh: boolean;
  refreshTick: number;
  fullscreenWidgetId: string | null;
  onRemoveWidget: (id: string) => void;
  onAddDataQuality: () => void;
  onAddStatSummary: () => void;
  onClearDashboard: () => void;
  onLayoutChange: (layouts: Layout) => void;
  onUpdateTitle: (id: string, title: string) => void;
  onToggleLock: (id: string) => void;
  onToggleCollapse: (id: string) => void;
  onSwitchDashboard: (id: string) => void;
  onCreateDashboard: (name: string) => void;
  onRenameDashboard: (id: string, name: string) => void;
  onDeleteDashboard: (id: string) => void;
  onDuplicateDashboard: (id: string) => void;
  onAddAnalyticsWidget: (type: WidgetType) => void;
  onAddAllAnalytics: () => void;
  onApplyTemplate: (template: DashboardTemplate) => void;
  onToggleAutoRefresh: () => void;
  onSetFullscreenWidget: (id: string | null) => void;
  onSortWidgets: () => void;
  onUndoRemove: () => boolean;
  onExportDashboard: () => void;
  onImportDashboard: (json: string) => boolean;
  onSetWidgetColorTag: (id: string, color: string | undefined) => void;
  onSetWidgetNote: (id: string, note: string) => void;
  onSetWidgetSize: (id: string, preset: 'small' | 'medium' | 'large') => void;
  onMoveWidgetToDashboard: (widgetId: string, targetDashboardId: string) => void;
}

function DashboardView({
  widgets,
  dashboards,
  activeDashboardId,
  activeDashboardName,
  autoRefresh,
  refreshTick,
  fullscreenWidgetId,
  onRemoveWidget,
  onAddDataQuality,
  onAddStatSummary,
  onClearDashboard,
  onLayoutChange,
  onUpdateTitle,
  onToggleLock,
  onToggleCollapse,
  onSwitchDashboard,
  onCreateDashboard,
  onRenameDashboard,
  onDeleteDashboard,
  onDuplicateDashboard,
  onAddAnalyticsWidget,
  onAddAllAnalytics,
  onApplyTemplate,
  onToggleAutoRefresh,
  onSetFullscreenWidget,
  onSortWidgets,
  onUndoRemove,
  onExportDashboard,
  onImportDashboard,
  onSetWidgetColorTag,
  onSetWidgetNote,
  onSetWidgetSize,
  onMoveWidgetToDashboard,
}: DashboardViewProps) {
  const { width, containerRef, mounted } = useContainerWidth({ initialWidth: 1200 });
  const [showAnalyticsMenu, setShowAnalyticsMenu] = useState(false);
  const [showTemplateMenu, setShowTemplateMenu] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [toolbarCollapsed, setToolbarCollapsed] = useState(false);
  const { toasts, addToast } = useToast();
  const importFileRef = useRef<HTMLInputElement>(null);

  // Keyboard shortcuts: Escape closes fullscreen
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && fullscreenWidgetId) {
        onSetFullscreenWidget(null);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [fullscreenWidgetId, onSetFullscreenWidget]);

  // Close dropdowns when clicking outside
  useEffect(() => {
    if (!showAnalyticsMenu && !showTemplateMenu) return;
    const handler = () => { setShowAnalyticsMenu(false); setShowTemplateMenu(false); };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [showAnalyticsMenu, showTemplateMenu]);

  const fullscreenWidget = fullscreenWidgetId ? widgets.find(w => w.id === fullscreenWidgetId) : null;

  const layouts = useMemo<ResponsiveLayouts>(() => {
    const lg: Layout = widgets.map((w) => ({
      i: w.id,
      x: w.layout?.x ?? 0,
      y: w.layout?.y ?? Infinity,
      w: w.layout?.w ?? 6,
      h: w.layout?.h ?? 4,
      minW: w.layout?.minW ?? 2,
      minH: w.layout?.minH ?? 2,
      static: w.locked ?? false,
    }));
    return { lg, md: lg, sm: lg, xs: lg };
  }, [widgets]);

  const handleLayoutChange = useCallback((_currentLayout: Layout, allLayouts: ResponsiveLayouts) => {
    const lgLayout = allLayouts.lg ?? _currentLayout;
    onLayoutChange(lgLayout);
  }, [onLayoutChange]);

  const handleNewDashboard = useCallback(() => {
    const name = prompt('Dashboard name:');
    if (name?.trim()) {
      onCreateDashboard(name.trim());
      addToast(`Dashboard "${name.trim()}" created`);
    }
  }, [onCreateDashboard, addToast]);

  const startRename = useCallback(() => {
    setRenamingId(activeDashboardId);
    setRenameValue(activeDashboardName);
  }, [activeDashboardId, activeDashboardName]);

  const commitRename = useCallback(() => {
    if (renamingId && renameValue.trim()) {
      onRenameDashboard(renamingId, renameValue.trim());
      addToast('Dashboard renamed');
    }
    setRenamingId(null);
  }, [renamingId, renameValue, onRenameDashboard, addToast]);

  const handleAddDataQuality = useCallback(() => {
    onAddDataQuality();
    addToast('Data Quality widget added');
  }, [onAddDataQuality, addToast]);

  const handleAddStatSummary = useCallback(() => {
    onAddStatSummary();
    addToast('Stats Summary widget added');
  }, [onAddStatSummary, addToast]);

  const handleAddAllAnalytics = useCallback(() => {
    onAddAllAnalytics();
    addToast('Analytics widgets added');
  }, [onAddAllAnalytics, addToast]);

  const handleApplyTemplate = useCallback((t: DashboardTemplate) => {
    if (widgets.length === 0 || confirm(`Replace current widgets with "${t.name}" template?`)) {
      onApplyTemplate(t);
      addToast(`"${t.name}" template applied`);
    }
  }, [widgets.length, onApplyTemplate, addToast]);

  const handleSort = useCallback(() => {
    onSortWidgets();
    addToast('Widgets sorted by type');
  }, [onSortWidgets, addToast]);

  const handleDuplicate = useCallback(() => {
    onDuplicateDashboard(activeDashboardId);
    addToast('Dashboard duplicated');
  }, [onDuplicateDashboard, activeDashboardId, addToast]);

  const handleRemoveWidget = useCallback((id: string) => {
    onRemoveWidget(id);
    addToast('Widget removed — click Undo in toolbar to restore');
  }, [onRemoveWidget, addToast]);

  const handleUndo = useCallback(() => {
    const restored = onUndoRemove();
    if (restored) addToast('Widget restored');
    else addToast('Nothing to undo');
  }, [onUndoRemove, addToast]);

  const handleImportFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const ok = onImportDashboard(reader.result as string);
      addToast(ok ? 'Dashboard imported' : 'Invalid dashboard file');
    };
    reader.readAsText(file);
    e.target.value = '';
  }, [onImportDashboard, addToast]);

  const handlePrint = useCallback(() => { window.print(); }, []);

  const otherDashboards = useMemo(() =>
    dashboards.filter(d => d.id !== activeDashboardId).map(d => ({ id: d.id, name: d.name })),
    [dashboards, activeDashboardId]
  );

  // Dashboard stats
  const widgetTypeCount = useMemo(() => {
    const counts = new Map<string, number>();
    widgets.forEach(w => counts.set(w.type, (counts.get(w.type) || 0) + 1));
    return counts;
  }, [widgets]);

  return (
    <div className="dashboard-view" ref={containerRef}>
      {/* Toast notifications */}
      {toasts.length > 0 && (
        <div className="dashboard-toast-container">
          {toasts.map(t => (
            <div key={t.id} className="dashboard-toast">{t.message}</div>
          ))}
        </div>
      )}

      {/* Fullscreen modal */}
      {fullscreenWidget && (
        <div className="dashboard-fullscreen-overlay" onClick={() => onSetFullscreenWidget(null)}>
          <div className="dashboard-fullscreen-card" onClick={(e) => e.stopPropagation()}>
            <div className="dashboard-fullscreen-header">
              <span className="dashboard-fullscreen-title">{fullscreenWidget.title}</span>
              <button className="dashboard-fullscreen-close" onClick={() => onSetFullscreenWidget(null)}>&#x2715;</button>
            </div>
            <div className="dashboard-fullscreen-body">
              <WidgetBody widget={fullscreenWidget} refreshTick={refreshTick} />
            </div>
          </div>
        </div>
      )}

      {/* Dashboard switcher */}
      <div className="dashboard-switcher">
        <select
          className="dashboard-select"
          value={activeDashboardId}
          onChange={(e) => onSwitchDashboard(e.target.value)}
        >
          {dashboards.map(d => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <button className="topbar-btn small" onClick={handleNewDashboard} title="Create new dashboard">+ New</button>
        <button className="topbar-btn small" onClick={handleDuplicate} title="Duplicate dashboard">Duplicate</button>
        <button className="topbar-btn small" onClick={startRename} title="Rename dashboard">Rename</button>
        {dashboards.length > 1 && (
          <button
            className="topbar-btn small secondary"
            onClick={() => { if (confirm(`Delete "${activeDashboardName}"?`)) { onDeleteDashboard(activeDashboardId); addToast('Dashboard deleted'); } }}
            title="Delete dashboard"
          >Delete</button>
        )}
      </div>

      {/* Rename inline */}
      {renamingId && (
        <div className="dashboard-rename-bar">
          <input
            className="dashboard-rename-input"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenamingId(null); }}
            autoFocus
          />
          <button className="topbar-btn small" onClick={commitRename}>Save</button>
          <button className="topbar-btn small secondary" onClick={() => setRenamingId(null)}>Cancel</button>
        </div>
      )}

      {/* Toolbar */}
      <div className={`dashboard-toolbar${toolbarCollapsed ? ' toolbar-collapsed' : ''}`}>
        <div className="dashboard-toolbar-left">
          <span className="dashboard-toolbar-title">{activeDashboardName}</span>
          {widgets.length > 0 && (
            <span className="dashboard-widget-count">
              {widgets.length} widget{widgets.length !== 1 ? 's' : ''}
            </span>
          )}
          <button
            className="dashboard-toolbar-toggle"
            onClick={() => setToolbarCollapsed(!toolbarCollapsed)}
            title={toolbarCollapsed ? 'Expand toolbar' : 'Collapse toolbar'}
          >
            {toolbarCollapsed ? '\u25BC' : '\u25B2'}
          </button>
        </div>
        {!toolbarCollapsed && (
          <div className="dashboard-actions">
            <button className="topbar-btn" onClick={handleAddDataQuality}>+ Data Quality</button>
            <button className="topbar-btn" onClick={handleAddStatSummary}>+ Stats Summary</button>

            {/* Analytics dropdown */}
            <div className="dashboard-analytics-dropdown" onClick={(e) => e.stopPropagation()}>
              <button className="topbar-btn" onClick={() => { setShowAnalyticsMenu(!showAnalyticsMenu); setShowTemplateMenu(false); }}>
                + Analytics {showAnalyticsMenu ? '\u25B2' : '\u25BC'}
              </button>
              {showAnalyticsMenu && (
                <div className="dashboard-analytics-menu">
                  <button className="dashboard-analytics-menu-item highlight" onClick={() => { handleAddAllAnalytics(); setShowAnalyticsMenu(false); }}>
                    Add All Analytics
                  </button>
                  {ANALYTICS_WIDGET_TYPES.map(t => (
                    <button
                      key={t}
                      className="dashboard-analytics-menu-item"
                      onClick={() => { onAddAnalyticsWidget(t); setShowAnalyticsMenu(false); addToast(`${ANALYTICS_WIDGET_LABELS[t]} added`); }}
                      disabled={widgets.some(w => w.type === t)}
                    >
                      {ANALYTICS_WIDGET_LABELS[t]}
                      {widgets.some(w => w.type === t) && ' \u2713'}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Templates dropdown */}
            <div className="dashboard-analytics-dropdown" onClick={(e) => e.stopPropagation()}>
              <button className="topbar-btn" onClick={() => { setShowTemplateMenu(!showTemplateMenu); setShowAnalyticsMenu(false); }}>
                Templates {showTemplateMenu ? '\u25B2' : '\u25BC'}
              </button>
              {showTemplateMenu && (
                <div className="dashboard-analytics-menu">
                  {DASHBOARD_TEMPLATES.map(t => (
                    <button
                      key={t.id}
                      className="dashboard-analytics-menu-item"
                      onClick={() => { handleApplyTemplate(t); setShowTemplateMenu(false); }}
                    >
                      <strong>{t.name}</strong>
                      <br />
                      <span style={{ fontSize: '10px', opacity: 0.6 }}>{t.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <button
              className={`topbar-btn ${autoRefresh ? 'active' : ''}`}
              onClick={onToggleAutoRefresh}
              title={autoRefresh ? 'Auto-refresh ON (30s)' : 'Enable auto-refresh'}
            >
              {autoRefresh && <span className="auto-refresh-dot" />}
              Auto-Refresh
            </button>
            <button className="topbar-btn" onClick={() => { onExportDashboard(); addToast('Dashboard exported'); }} title="Export dashboard as JSON">Export</button>
            <button className="topbar-btn" onClick={() => importFileRef.current?.click()} title="Import dashboard from JSON">Import</button>
            <input ref={importFileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportFile} />
            <button className="topbar-btn" onClick={handlePrint} title="Print dashboard">Print</button>
            {widgets.length > 0 && (
              <button className="topbar-btn" onClick={handleUndo} title="Undo last widget removal">Undo</button>
            )}
            {widgets.length > 1 && (
              <button className="topbar-btn" onClick={handleSort} title="Sort widgets by type">Sort</button>
            )}
            {widgets.length > 0 && (
              <button className="topbar-btn secondary" onClick={onClearDashboard}>Clear All</button>
            )}
          </div>
        )}
      </div>

      {/* Dashboard stats bar */}
      {widgets.length > 0 && (
        <div className="dashboard-stats-bar">
          <span>{widgets.length} widget{widgets.length !== 1 ? 's' : ''}</span>
          <span className="dashboard-stats-sep">|</span>
          {Array.from(widgetTypeCount.entries()).map(([type, count]) => (
            <span key={type} className="dashboard-stats-chip">{type}: {count}</span>
          ))}
        </div>
      )}

      {widgets.length === 0 ? (
        <div className="dashboard-empty">
          <div className="dashboard-empty-icon">&#x1F4CA;</div>
          <p className="dashboard-empty-title">Your dashboard is empty</p>
          <p className="dashboard-empty-hint">
            Get started quickly with a template, or add individual widgets.
            You can also pin charts from chat queries and road stats from the map.
          </p>
          <div className="dashboard-empty-actions">
            {DASHBOARD_TEMPLATES.slice(0, 3).map(t => (
              <button key={t.id} className="dashboard-empty-btn" onClick={() => handleApplyTemplate(t)}>
                {t.name}
              </button>
            ))}
          </div>
          <div className="dashboard-empty-actions" style={{ marginTop: 4 }}>
            <button className="dashboard-empty-btn" onClick={handleAddAllAnalytics}>+ All Analytics</button>
            <button className="dashboard-empty-btn" onClick={handleAddDataQuality}>+ Data Quality</button>
            <button className="dashboard-empty-btn" onClick={handleAddStatSummary}>+ Stats Summary</button>
          </div>
        </div>
      ) : mounted ? (
        <ResponsiveGridLayout
          className="dashboard-grid-layout"
          width={width}
          layouts={layouts}
          breakpoints={BREAKPOINTS}
          cols={COLS}
          rowHeight={ROW_HEIGHT}
          onLayoutChange={handleLayoutChange}
          dragConfig={{ enabled: true, bounded: false, handle: '.dashboard-widget-drag-handle', threshold: 3 }}
          resizeConfig={{ enabled: true, handles: ['se'] }}
          compactor={verticalCompactor}
          margin={[16, 16]}
          containerPadding={[0, 0]}
          autoSize={true}
        >
          {widgets.map(widget => (
            <div key={widget.id}>
              <DashboardWidgetCard
                widget={widget}
                onRemove={() => handleRemoveWidget(widget.id)}
                onUpdateTitle={(title) => onUpdateTitle(widget.id, title)}
                onToggleLock={() => onToggleLock(widget.id)}
                onToggleCollapse={() => onToggleCollapse(widget.id)}
                onFullscreen={() => onSetFullscreenWidget(widget.id)}
                onSetColorTag={(color) => onSetWidgetColorTag(widget.id, color)}
                onSetNote={(note) => onSetWidgetNote(widget.id, note)}
                onSetSize={(preset) => onSetWidgetSize(widget.id, preset)}
                onMoveTo={(targetId) => onMoveWidgetToDashboard(widget.id, targetId)}
                otherDashboards={otherDashboards}
                refreshTick={refreshTick}
              />
            </div>
          ))}
        </ResponsiveGridLayout>
      ) : null}
    </div>
  );
}

export default DashboardView;

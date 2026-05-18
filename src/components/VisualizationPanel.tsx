import { useState, useEffect, useRef } from 'react';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, ArcElement, Title, Tooltip, Legend } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import './VisualizationPanel.css';
import type { GeneratedChartPayload, ChartEditMeta, ChartBarClickContext } from '../types/charts';
import { applyPipeline, editFigure } from '../experimental/chart-editing/api/chartClient';
import ChartRenderer from './ChartRenderer';
import { formatTimeHuman } from '../utils/dateTime';

ChartJS.register(CategoryScale, LinearScale, BarElement, ArcElement, Title, Tooltip, Legend);

interface Visualization {
  id: string;
  type: 'speed' | 'road' | 'violations';
  title: string;
  data: any[];
  timestamp: Date;
}

interface GeneratedChart {
  id: string;
  title: string;
  payload: GeneratedChartPayload;
  timestamp: Date;
}

interface VisualizationPanelProps {
  visualizations: Visualization[];
  onRemove: (id: string) => void;
  onClear: () => void;
  generatedCharts?: GeneratedChart[];
  onRemoveGenerated?: (id: string) => void;
  onClearGenerated?: () => void;
  onGenerateCharts?: (payloads: GeneratedChartPayload[]) => void;
  onReplaceGenerated?: (id: string, payload: GeneratedChartPayload, title?: string) => void;
  onPinToDashboard?: (title: string, payload: GeneratedChartPayload) => void;
  onGeneratedChartBarClick?: (chart: GeneratedChart, context: ChartBarClickContext) => void;
  chartEditingEnabled?: boolean;
  chartEditingDisabledReason?: string;
}

interface EditMessage {
  role: 'user' | 'assistant';
  content: string;
}


const VisualizationPanel: React.FC<VisualizationPanelProps> = ({
  visualizations,
  onRemove,
  onClear,
  generatedCharts = [],
  onRemoveGenerated,
  onClearGenerated,
  onGenerateCharts,
  onReplaceGenerated,
  onPinToDashboard,
  onGeneratedChartBarClick,
  chartEditingEnabled = false,
  chartEditingDisabledReason,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isPanelFullscreen, setIsPanelFullscreen] = useState(false);
  const [selectedViz, setSelectedViz] = useState<string | null>(null);
  const [editingChart, setEditingChart] = useState<GeneratedChart | null>(null);
  const [editMessages, setEditMessages] = useState<EditMessage[]>([]);
  const [editInput, setEditInput] = useState('');
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false);
  const [paramEdits, setParamEdits] = useState<Record<string, number | string>>({});
  const [filterSelections, setFilterSelections] = useState<Record<string, Set<string | number>>>({});
  const [isApplyingDirect, setIsApplyingDirect] = useState(false);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [showChat, setShowChat] = useState(false);
  const AI_EDIT_ENABLED = chartEditingEnabled;
  const [initialParamEdits, setInitialParamEdits] = useState<Record<string, number | string>>({});
  const [initialFilterSelections, setInitialFilterSelections] = useState<Record<string, Set<string | number>>>({});
  const prevGeneratedCountRef = useRef<number>(generatedCharts.length);
  const hasMountedRef = useRef(false);

  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      prevGeneratedCountRef.current = generatedCharts.length;
      return;
    }
    if (generatedCharts.length > prevGeneratedCountRef.current) {
      setIsOpen(true);
    }
    prevGeneratedCountRef.current = generatedCharts.length;
  }, [generatedCharts.length]);

  // Handle ESC key to exit fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isPanelFullscreen) {
        setIsPanelFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isPanelFullscreen]);

  const generateChartData = (viz: Visualization) => {
    if (viz.type === 'speed') {
      const bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
      const counts = new Array(bins.length - 1).fill(0);
      const labels = bins.slice(0, -1).map((b, i) => `${b}-${bins[i + 1]}`);

      viz.data.forEach(v => {
        for (let i = 0; i < bins.length - 1; i++) {
          if (v.speed >= bins[i] && v.speed < bins[i + 1]) {
            counts[i]++;
            break;
          }
        }
      });

      return {
        labels,
        datasets: [{
          label: 'Vehicles',
          data: counts,
          backgroundColor: counts.map((_, i) => {
            const colors = ['#81c784', '#a5d6a7', '#ffb74d', '#ffa726', '#ef9a9a', '#e57373', '#f48fb1', '#ce93d8', '#9fa8da', '#90caf9'];
            return colors[i];
          }),
          borderColor: '#81c784',
          borderWidth: 1,
        }]
      };
    }

    return { labels: [], datasets: [] };
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      title: {
        display: false,
      },
      tooltip: {
        backgroundColor: 'rgba(10, 14, 39, 0.95)',
        titleColor: '#81c784',
        bodyColor: '#fff',
        borderColor: '#81c784',
        borderWidth: 1,
        padding: 8,
        displayColors: false,
      }
    },
    scales: {
      x: {
        grid: { color: 'rgba(100, 255, 218, 0.1)' },
        ticks: { color: '#ccd6f6', font: { size: 9 } },
      },
      y: {
        grid: { color: 'rgba(100, 255, 218, 0.1)' },
        ticks: { color: '#ccd6f6', font: { size: 9 } },
        beginAtZero: true,
      }
    }
  };

  const getChartMeta = (chart: GeneratedChart | null): ChartEditMeta | undefined =>
    chart?.payload?.meta;

  const editingMeta = editingChart ? getChartMeta(editingChart) : undefined;

  const openEditDialog = (chart: GeneratedChart) => {
    setEditingChart(chart);
    const meta = getChartMeta(chart);
    const baseTitle = chart.title || 'Generated Chart';
    
    let greeting = `Editing "${baseTitle}".`;
    
    const hasParams = meta?.editableParams?.length;
    const hasFilters = meta?.filterableParams?.length;
    
    if (hasParams && hasFilters) {
      greeting += ' You can adjust parameters or add filters to refine the data.';
    } else if (hasParams) {
      greeting += ' You can adjust the parameters shown above.';
    } else if (hasFilters) {
      greeting += ' You can add filters to refine the data.';
    } else {
      greeting += ' Describe what you\'d like to change.';
    }
    
    setEditMessages([{
      role: 'assistant',
      content: greeting,
    }]);
    setEditInput('');
    setIsSubmittingEdit(false);

    // Prime direct-edit controls
    const initialParams: Record<string, number | string> = {};
    meta?.editableParams?.forEach((p) => {
      if (typeof p.value !== 'undefined' && p.value !== null) {
        initialParams[p.key] = p.value as any;
      }
    });
    setParamEdits(initialParams);
    setInitialParamEdits(initialParams);

    const initialFilters: Record<string, Set<string | number>> = {};
    const baselineFilters = meta?.originalFilters || meta?.currentFilters || [];
    (baselineFilters || []).forEach((f: any) => {
      const columnKey = f.column || f.key;
      if (!columnKey) return;
      if (f.operator === 'in' && Array.isArray(f.value)) {
        initialFilters[columnKey] = new Set(f.value);
      } else if (typeof f.value !== 'undefined' && f.value !== null) {
        initialFilters[columnKey] = new Set([f.value]);
      }
    });
    setFilterSelections(initialFilters);
    setInitialFilterSelections(initialFilters);
    setApplyMessage(null);
    setShowChat(false);
  };

  const getRelevantFilterableParams = (chart: GeneratedChart) => {
    const meta = getChartMeta(chart);
    if (!meta?.filterableParams) return [];
    
    // Use columnsUsed from backend if available
    const columnsUsed = meta.columnsUsed || [];
    if (columnsUsed.length === 0) return [];
    
    // Convert to lowercase for matching
    const usedColumnsSet = new Set(columnsUsed.map((col: string) => col.toLowerCase()));
    
    // Filter to only show params for columns actually used in this chart
    return meta.filterableParams.filter((param: any) => {
      const column = (param.column || '').toLowerCase();

      // Hide weekday_idx to avoid duplicate weekday controls
      if (column === 'weekday_idx') return false;
      
      // Include if this column is used in the chart data
      if (usedColumnsSet.has(column)) return true;
      
      // Also include related columns
      // e.g., if chart uses 'weekday', also show 'weekday_idx' filter
      if (column === 'weekday_idx' && usedColumnsSet.has('weekday')) return true;
      if (column === 'weekday' && usedColumnsSet.has('weekday_idx')) return true;
      
      // If chart uses 'road', it's about roads
      if (usedColumnsSet.has('road') && column === 'road') return true;
      
      return false;
    });
  };

  const toggleFilterValue = (column: string, value: string | number) => {
    setFilterSelections(prev => {
      const existing = prev[column] ? new Set(prev[column]) : new Set<string | number>();
      if (existing.has(value)) {
        existing.delete(value);
      } else {
        existing.add(value);
      }
      return { ...prev, [column]: existing };
    });
  };

  const closeEditDialog = () => {
    setEditingChart(null);
    setEditMessages([]);
    setEditInput('');
    setIsSubmittingEdit(false);
    setParamEdits({});
    setFilterSelections({});
    setApplyMessage(null);
    setShowChat(false);
  };

  const sendEditMessage = async () => {
    if (!editingChart) return;
    const trimmed = editInput.trim();
    if (!trimmed) return;

    const userMessage: EditMessage = { role: 'user', content: trimmed };
    const updatedHistory: EditMessage[] = [...editMessages, userMessage];
    setEditMessages(updatedHistory);
    setEditInput('');
    setIsSubmittingEdit(true);

    try {
      const data = await editFigure({
        chartId: editingChart.id,
        chartTitle: editingChart.title,
        instructions: trimmed,
        chartPayload: editingChart.payload,
        meta: editingMeta,
        history: editMessages,
      }) as any;
      const applied = Array.isArray(data.appliedParams) ? data.appliedParams : [];
      const appliedFilters = Array.isArray(data.appliedFilters) ? data.appliedFilters : [];
      
      const detail = applied.length
        ? '\n' + applied
            .map((item: any) => `• ${item.label || item.key}: ${item.previousValue ?? '—'} → ${item.value}`)
            .join('\n')
        : '';
      
      const refreshedCharts = data.pipelineResult?.chart_payload;
      const chartRole = editingMeta?.chartRole;
      let replacedChart = false;

      if (Array.isArray(refreshedCharts) && refreshedCharts.length > 0 && chartRole) {
        const updatedPayload = refreshedCharts.find((payload: GeneratedChartPayload) => payload?.meta?.chartRole === chartRole);
        if (updatedPayload) {
          onReplaceGenerated?.(editingChart.id, updatedPayload, updatedPayload.title);
          replacedChart = true;
        }
      }

      if (!replacedChart && Array.isArray(refreshedCharts) && refreshedCharts.length > 0) {
        onGenerateCharts?.(refreshedCharts);
      }

      const assistantMessageBase = data.assistantMessage || 'Noted.';
      const generationNote = replacedChart
        ? '\n\n✅ Chart updated with your changes.'
        : (applied.length || appliedFilters.length) ? '\n\n⏳ Generating updated chart...' : '';
      const assistantMessage = assistantMessageBase.concat(detail, generationNote);
      setEditMessages(prev => [...prev, { role: 'assistant', content: assistantMessage }]);
    } catch (error: any) {
      console.error('Figure edit error:', error);
      setEditMessages(prev => [...prev, { role: 'assistant', content: `Error: ${error.message || 'Unknown error.'}` }]);
    } finally {
      setIsSubmittingEdit(false);
    }
  };

  const applyDirectEdits = async (override?: {
    paramEdits?: Record<string, number | string>;
    filterSelections?: Record<string, Set<string | number>>;
    replaceFilters?: boolean;
  }) => {
    if (!editingChart) return;
    if (!editingMeta?.pipelineId) {
      setApplyMessage('No pipeline associated with this chart.');
      return;
    }

    const paramSource = override?.paramEdits ?? paramEdits;
    const filterSource = override?.filterSelections ?? filterSelections;

    const appliedParams = (editingMeta?.editableParams || [])
      .map((param: any) => {
        const newValue = paramSource[param.key];
        let value: any = newValue;
        if (typeof newValue === 'string' && ['number', 'integer', 'slider', 'percent'].includes(param.type)) {
          const parsed = Number(newValue);
          if (!Number.isNaN(parsed)) {
            value = param.type === 'integer' ? Math.round(parsed) : parsed;
          }
        }
        if (typeof newValue === 'number' && param.type === 'integer') {
          value = Math.round(newValue);
        }
        if (typeof value === 'undefined' || value === null || value === param.value) {
          return null;
        }
        return {
          key: param.key,
          label: param.label || param.key,
          value,
          previousValue: param.value,
        };
      })
      .filter(Boolean) as any[];

    const relevantParams = editingChart ? getRelevantFilterableParams(editingChart) : [];
    const selectedFilters = relevantParams
      .map((param: any) => {
        const selection = filterSource[param.column || param.key];
        if (!selection || selection.size === 0) return null;
        const valueArray = Array.from(selection);
        return {
          column: param.column || param.key,
          operator: 'in',
          value: valueArray,
        };
      })
      .filter(Boolean) as any[];
    const baselineFilters = (editingMeta as any)?.originalFilters || (editingMeta as any)?.currentFilters || [];
    const baselineFormatted = Array.isArray(baselineFilters)
      ? baselineFilters
          .map((f: any) => ({
            column: f.column || f.key,
            operator: f.operator,
            value: f.value,
          }))
          .filter((f: any) => f.column && f.operator)
      : [];
    const combinedFilters = [...baselineFormatted, ...selectedFilters];

    setIsApplyingDirect(true);
    setApplyMessage(null);

    try {
      const data = await applyPipeline(editingMeta.pipelineId, {
        chartId: editingChart.id,
        chartTitle: editingChart.title,
        chartPayload: editingChart.payload,
        meta: editingMeta,
        appliedParams,
        appliedFilters: combinedFilters,
        replaceFilters: override?.replaceFilters ?? true,
      }) as any;
      const refreshedCharts = data.pipelineResult?.chart_payload;
      const chartRole = editingMeta?.chartRole;
      let replacedChart = false;

      if (Array.isArray(refreshedCharts) && refreshedCharts.length > 0 && chartRole) {
        const updatedPayload = refreshedCharts.find((payload: GeneratedChartPayload) => payload?.meta?.chartRole === chartRole);
        if (updatedPayload) {
          onReplaceGenerated?.(editingChart.id, updatedPayload, updatedPayload.title);
          replacedChart = true;
        }
      }

      if (!replacedChart && Array.isArray(refreshedCharts) && refreshedCharts.length > 0) {
        onGenerateCharts?.(refreshedCharts);
      }

      const appliedSummary = data.assistantMessage || 'Updates applied.';
      setApplyMessage(appliedSummary);
      closeEditDialog();
    } catch (err: any) {
      setApplyMessage(err?.message || 'Failed to apply updates.');
    } finally {
      setIsApplyingDirect(false);
    }
  };

  const resetEdits = () => {
    setParamEdits(initialParamEdits);
    const clonedFilters: Record<string, Set<string | number>> = {};
    Object.entries(initialFilterSelections).forEach(([key, val]) => {
      clonedFilters[key] = new Set(val);
    });
    setFilterSelections(clonedFilters);
    setApplyMessage(null);
    void applyDirectEdits({ paramEdits: initialParamEdits, filterSelections: clonedFilters, replaceFilters: true });
  };

  const renderGeneratedChartContent = (chart: GeneratedChart, variant: 'preview' | 'fullscreen') => {
    return (
      <ChartRenderer
        payload={chart.payload}
        title={chart.title || 'Generated Chart'}
        variant={variant}
        onBarClick={(context) => {
          onGeneratedChartBarClick?.(chart, context);
          if (chart.payload.meta?.chartRole === 'area_top_unique_segments') {
            setSelectedViz(null);
          }
        }}
      />
    );
  };

  return (
    <>
      {!isOpen && (visualizations.length > 0 || generatedCharts.length > 0) && (
        <button className="viz-panel-trigger" onClick={() => setIsOpen(true)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 3v18h18M7 16l4-4 4 4 6-6" />
          </svg>
          <span className="viz-count">{visualizations.length + generatedCharts.length}</span>
        </button>
      )}

      <div className={`viz-panel ${isOpen ? 'open' : ''} ${isPanelFullscreen ? 'fullscreen' : ''}`}>
        <div className="viz-panel-header">
          <span>Visualizations</span>
          <div className="viz-panel-actions">
            {(visualizations.length > 0 || generatedCharts.length > 0) && (
              <>
                {!isPanelFullscreen && (
                  <button
                    onClick={() => setIsPanelFullscreen(true)}
                    className="viz-fullscreen-btn"
                    title="Fullscreen"
                  >
                    ⤢
                  </button>
                )}
                <button
                  onClick={() => {
                    onClear();
                    onClearGenerated?.();
                  }}
                  className="viz-clear-btn"
                  title="Clear all"
                >
                  🗑
                </button>
              </>
            )}
            {isPanelFullscreen ? (
              <button
                onClick={() => setIsPanelFullscreen(false)}
                className="viz-close-btn"
                title="Exit fullscreen"
              >
                ✕
              </button>
            ) : (
              <button onClick={() => setIsOpen(false)} className="viz-close-btn">
                ✕
              </button>
            )}
          </div>
        </div>

        <div className="viz-panel-content">
          {visualizations.length === 0 && generatedCharts.length === 0 ? (
            <div className="viz-empty">No visualizations yet</div>
          ) : (
            <>
              {visualizations.map(viz => (
                <div key={viz.id} className="viz-item">
                  <div className="viz-item-header">
                    <span className="viz-item-title">{viz.title}</span>
                    <button onClick={() => onRemove(viz.id)} className="viz-item-remove">
                      ✕
                    </button>
                  </div>
                  <div
                    className={`viz-item-preview ${selectedViz === viz.id ? 'expanded' : ''}`}
                    onClick={() => setSelectedViz(selectedViz === viz.id ? null : viz.id)}
                  >
                    <Bar data={generateChartData(viz)} options={options} />
                  </div>
                  <div className="viz-item-footer">
                    {formatTimeHuman(viz.timestamp, '—')}
                  </div>
                </div>
              ))}

              {generatedCharts.map(chart => {
                const chartMeta = getChartMeta(chart);
                const canEdit = AI_EDIT_ENABLED && Boolean(chartMeta?.editableParams?.length || chartMeta?.filterableParams?.length);
                const isCrashAnalysis = chartMeta?.chartRole === 'crash_analysis';
                return (
                <div key={chart.id} className="viz-item">
                  <div className="viz-item-header">
                    <span className="viz-item-title">{chart.title || 'Generated Chart'}</span>
                    <div className="viz-item-controls">
                      {onPinToDashboard && (
                        <button
                          className="viz-item-edit"
                          onClick={() => onPinToDashboard(chart.title || 'Chart', chart.payload)}
                          title="Pin to dashboard"
                        >
                          Pin
                        </button>
                      )}
                      {!isCrashAnalysis && (
                        <button
                          className="viz-item-edit"
                          onClick={() => canEdit && openEditDialog(chart)}
                          disabled={!canEdit}
                          title={
                            canEdit
                              ? 'Edit chart configuration'
                              : AI_EDIT_ENABLED
                                ? 'This chart does not expose editable parameters'
                                : (chartEditingDisabledReason || 'Chart editing is currently disabled.')
                          }
                        >
                          Edit
                        </button>
                      )}
                      {onRemoveGenerated && (
                        <button onClick={() => onRemoveGenerated(chart.id)} className="viz-item-remove">
                          ✕
                        </button>
                      )}
                    </div>
                  </div>
                  <div
                    className={`viz-item-preview ${selectedViz === chart.id ? 'expanded' : ''}`}
                    onClick={() => setSelectedViz(chart.id)}
                    title="Click to expand"
                  >
                    {renderGeneratedChartContent(chart, 'preview')}
                    <div className="viz-item-expand-icon">⤢</div>
                  </div>
                  <div className="viz-item-footer">
                    {formatTimeHuman(chart.timestamp, '—')}
                  </div>
                </div>
              )})}
            </>
          )}
        </div>
      </div>

      {selectedViz && (
        <div className="viz-chart-modal-overlay" onClick={() => setSelectedViz(null)}>
          <div className="viz-chart-modal" onClick={(e) => e.stopPropagation()}>
            <div className="viz-chart-modal-header">
              <h3 className="viz-chart-modal-title">
                {(() => {
                  const viz = visualizations.find(v => v.id === selectedViz);
                  if (viz) return viz.title;
                  const chart = generatedCharts.find(c => c.id === selectedViz);
                  return chart?.title || 'Chart';
                })()}
              </h3>
              <div className="viz-chart-modal-actions">
                {(() => {
                  const selectedGenerated = generatedCharts.find(c => c.id === selectedViz);
                  const meta = selectedGenerated ? getChartMeta(selectedGenerated) : undefined;
                  const isCrashAnalysis = meta?.chartRole === 'crash_analysis';
                  if (
                    selectedGenerated &&
                    AI_EDIT_ENABLED &&
                    !isCrashAnalysis &&
                    (meta?.editableParams?.length || meta?.filterableParams?.length)
                  ) {
                    return (
                      <button
                        className="viz-chart-modal-edit"
                        onClick={() => openEditDialog(selectedGenerated)}
                      >
                        Edit
                      </button>
                    );
                  }
                  return null;
                })()}
                <button className="viz-chart-modal-close" onClick={() => setSelectedViz(null)}>
                  ✕
                </button>
              </div>
            </div>
            <div className="viz-chart-modal-content">
              {visualizations.some(v => v.id === selectedViz) ? (
                <Bar
                  data={generateChartData(visualizations.find(v => v.id === selectedViz)!)}
                  options={{
                    ...options,
                    plugins: {
                      ...options.plugins,
                      title: { display: false }
                    }
                  }}
                />
              ) : (
                (() => {
                  const chart = generatedCharts.find(c => c.id === selectedViz);
                  if (!chart) return null;
                  return renderGeneratedChartContent(chart, 'fullscreen');
                })()
              )}
            </div>
          </div>
        </div>
      )}

      {editingChart && (
        <div className="viz-edit-overlay" onClick={closeEditDialog}>
          <div
            className="viz-edit-content"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="viz-edit-header">
              <h3 className="viz-edit-title">Edit Chart</h3>
              <button className="viz-edit-close" onClick={closeEditDialog}>
                ✕
              </button>
            </div>

            <div className="viz-edit-subtitle">{editingChart.title || editingChart.id}</div>

            {/* Add this section for description */}
            {editingMeta?.description && (
              <div className="viz-edit-description">
                {editingMeta.description}
              </div>
            )}

            <div className="viz-edit-params-container">
              <div className="viz-edit-section-note">Pick filters or tweak parameters, then Apply to refresh.</div>

              {editingMeta?.editableParams?.length ? (
                <div className="viz-edit-section">
                  <div className="viz-edit-section-title">Parameters</div>
                  <div className="viz-edit-params-list">
                    {editingMeta.editableParams.map((param) => (
                      <div key={param.key} className="viz-edit-param-item">
                        <span className="viz-edit-param-label">{param.label || param.key}</span>
                        <input
                          className="viz-edit-param-input"
                          type="number"
                          min={param.min}
                          max={param.max}
                          step={param.step || (param.type === 'integer' ? 1 : 0.1)}
                          value={paramEdits[param.key] ?? param.value ?? ''}
                          onChange={(e) => {
                            const nextVal = e.target.value;
                            setParamEdits(prev => ({ ...prev, [param.key]: nextVal }));
                          }}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {(() => {
                const relevantParams = editingChart ? getRelevantFilterableParams(editingChart) : [];
                return relevantParams.length ? (
                  <div className="viz-edit-section">
                    <div className="viz-edit-section-title">Available Filters</div>
                    <div className="viz-edit-filters-grid">
                      {relevantParams.map((param: any) => {
                        const options = Array.isArray(param.options) ? param.options : [];
                        const canToggle = options.length > 0 && options.length <= 32;
                        const canGenerateRange = !canToggle
                          && typeof param.min === 'number'
                          && typeof param.max === 'number'
                          && Number.isFinite(param.min)
                          && Number.isFinite(param.max)
                          && param.max - param.min <= 48
                          && param.type === 'integer';
                        const rangeOptions = canGenerateRange
                          ? Array.from({ length: (param.max - param.min + 1) }, (_, idx) => param.min + idx)
                          : [];
                        const selected = filterSelections[param.column || param.key] || new Set<string | number>();
                        return (
                          <div key={param.key} className="viz-edit-filter-chip">
                            <span className="viz-edit-filter-name">{param.label || param.column}</span>
                            <span className="viz-edit-filter-type">{param.type}</span>
                            {canToggle || canGenerateRange ? (
                              <div className="viz-edit-filter-options">
                                {(canToggle ? options : rangeOptions).map((opt: any) => {
                                  const display = typeof opt === 'string' ? opt : opt?.toString();
                                  const isSelected = selected.has(opt);
                                  return (
                                    <button
                                      key={`${param.key}-${opt}`}
                                      className={`viz-edit-filter-option ${isSelected ? 'selected' : ''}`}
                                      onClick={() => toggleFilterValue(param.column || param.key, opt)}
                                    >
                                      {display}
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                    <div className="viz-edit-filter-examples">
                      Examples: select hours/days or use the chat box for free-text filters
                    </div>
                  </div>
                ) : null;
              })()}

              {editingMeta?.currentFilters?.length ? (
                <div className="viz-edit-section">
                  <div className="viz-edit-section-title">Active Filters</div>
                  <div className="viz-edit-params-list">
                    {editingMeta.currentFilters.map((filter: any, idx: number) => (
                      <div key={idx} className="viz-edit-param-item">
                        <span className="viz-edit-param-label">{filter.column}</span>
                        <span className="viz-edit-param-value">
                          {filter.operator === 'is_weekend' ? 'weekends' : 
                           filter.operator === 'is_weekday' ? 'weekdays' :
                           `${filter.operator} ${Array.isArray(filter.value) ? `[${filter.value.join(', ')}]` : filter.value ?? '—'}`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="viz-edit-section">
              <div className="viz-edit-section-title"></div>
              <div className="viz-edit-direct-row">
                <button
                  className="viz-edit-send"
                  onClick={() => void applyDirectEdits()}
                  disabled={isApplyingDirect}
                >
                  {isApplyingDirect ? 'Applying...' : 'Apply Changes'}
                </button>
                <button
                  className="viz-edit-reset"
                  onClick={() => resetEdits()}
                  disabled={isApplyingDirect}
                >
                  Reset to original
                </button>
                {applyMessage && <div className="viz-edit-apply-message">{applyMessage}</div>}
              </div>
              <div className="viz-edit-hint">Adjust parameters or select filter options above, then apply directly for crash analysis charts.</div>
            </div>

            {AI_EDIT_ENABLED && (
              <>
                <div className="viz-edit-chat-toggle">
                  <button className="viz-edit-send secondary" onClick={() => setShowChat((prev) => !prev)}>
                    {showChat ? 'Hide AI Edit' : 'Need natural language? Open AI edit'}
                  </button>
                  <span className="viz-edit-hint">Optional: describe changes in plain language.</span>
                </div>

                {showChat && (
                  <div className="viz-edit-chat">
                    <div className="viz-edit-chat-header">
                      <div className="viz-edit-section-title">AI Edit (optional)</div>
                      <div className="viz-edit-hint">Describe changes in plain language.</div>
                    </div>
                    <div className="viz-edit-messages">
                      {editMessages.map((msg, idx) => (
                        <div key={`${msg.role}-${idx}`} className={`viz-edit-message ${msg.role}`}>
                          {msg.content}
                        </div>
                      ))}
                    </div>
                    <div className="viz-edit-input-container">
                      <textarea
                        className="viz-edit-input"
                        value={editInput}
                        onChange={(e) => setEditInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            if (!isSubmittingEdit && editInput.trim()) {
                              sendEditMessage();
                            }
                          }
                        }}
                        placeholder="e.g., 'only weekends', 'Main Street only'"
                        disabled={isSubmittingEdit}
                        rows={4}
                      />
                      <button
                        className="viz-edit-send"
                        onClick={sendEditMessage}
                        disabled={isSubmittingEdit || !editInput.trim()}
                      >
                        {isSubmittingEdit ? 'Working...' : 'Send'}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
};

export default VisualizationPanel;

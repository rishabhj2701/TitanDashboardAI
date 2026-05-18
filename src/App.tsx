import { useRef, useState, useCallback, useMemo, useEffect } from 'react';
import { useAuth } from './auth/AuthContext';
import type * as mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import './App.css';
import Map from './components/Map';
import MinimalChat from './components/MinimalChat';
import ChartOverlay from './components/ChartOverlay';
import VisualizationPanel from './components/VisualizationPanel';
import InfoOverlay from './components/InfoOverlay';
import AppTopBar from './components/layout/AppTopBar';
import MapLegend from './components/layout/MapLegend';
import StatsOverlay from './components/overlays/StatsOverlay';
import IngestionOverlay from './components/overlays/IngestionOverlay';
import DashboardView from './components/DashboardView';
import MapSearchBar from './components/MapSearchBar';
import KeyboardShortcutsOverlay from './components/KeyboardShortcutsOverlay';
import GuidedTour from './components/GuidedTour';
import ToastContainer from './components/ToastContainer';
import { useGlobalToast } from './hooks/useGlobalToast';
import { loadCvRoadAggregates } from './services/dataLoader';
import type { CvRoadAggregateGeoJSON } from './services/dataLoader';
import { clearAppState } from './utils/stateManager';
import { computeAvgUniqueVehiclesPerHourFromHourlyJson } from './utils/hourlyVehicles';
import { useCvRoadPinnedPanel } from './hooks/useCvRoadPinnedPanel';
import { useStatsPanel } from './hooks/useStatsPanel';
import { useCvRuns } from './hooks/useCvRuns';
import { useMapState } from './hooks/useMapState';
import { useAreaAnalysis } from './hooks/useAreaAnalysis';
import { useChartState } from './hooks/useChartState';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { useAppHandlers } from './hooks/useAppHandlers';
import { useMapUtilities } from './hooks/useMapUtilities';
import { useMapLayers } from './hooks/useMapLayers';
import { useMapInitialization } from './hooks/useMapInitialization';
import { useStatePersistence } from './hooks/useStatePersistence';
import { useDashboard } from './hooks/useDashboard';
import { useMapDataUpdates } from './hooks/useMapDataUpdates';
import { useMapEventHandlers } from './hooks/useMapEventHandlers';
import { useWorkzoneLines } from './hooks/useWorkzoneLines';
import { useCvRoadLines } from './hooks/useCvRoadLines';
import { useAreaAggregateRoadLines } from './hooks/useAreaAggregateRoadLines';
import { useRoadBounds } from './hooks/useRoadBounds';
import { usePolygonLayer } from './hooks/usePolygonLayer';
import { useMapErrorHandling } from './hooks/useMapErrorHandling';
import { useRoadSegmentFocusFilter } from './hooks/useRoadSegmentFocusFilter';
import { useMapHoverCoordinates } from './hooks/useMapHoverCoordinates';
import { roadColorModeToTileMode, type RoadColorMode } from './features/map/roadColorMode';
import type {
  AreaSummary,
} from './features/map/types';
import type { ExternalChatMessage } from './features/shared/types';
import type { ChartBarClickContext, GeneratedChartPayload } from './types/charts';

interface AppProps {
  chartEditingEnabled?: boolean;
  chartEditingDisabledReason?: string;
  areaAnalysisTimeoutMs?: number;
}

function App({ chartEditingEnabled = false, chartEditingDisabledReason, areaAnalysisTimeoutMs = 300000 }: AppProps) {
  const { user, logout } = useAuth();
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const popupRef = useRef<mapboxgl.Popup | null>(null);
  const clickPopupRef = useRef<mapboxgl.Popup | null>(null);
  
  const [roadColorMode, setRoadColorMode] = useState<RoadColorMode>('vehicle-count');

  // Hooks
  const cvRunsHook = useCvRuns(roadColorModeToTileMode(roadColorMode));
  const mapState = useMapState();
  const [mapReady, setMapReady] = useState(false);
  const [mapInitialized, setMapInitialized] = useState(false);
  const [cvRoadGeojson, setCvRoadGeojson] = useState<CvRoadAggregateGeoJSON | null>(null);
  const [cvRoadLoading, setCvRoadLoading] = useState(false);
  
  const areaAnalysis = useAreaAnalysis(mapRef, mapReady, areaAnalysisTimeoutMs, cvRunsHook.activeCvRunId);
  const chartState = useChartState(cvRunsHook.useRoadTiles);
  
  const [externalChatMessage, setExternalChatMessage] = useState<ExternalChatMessage | null>(null);
  const [hoverCoord, setHoverCoord] = useState<[number, number] | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const [showIngestion, setShowIngestion] = useState(false);
  const [activeView, setActiveView] = useState<'map' | 'dashboard'>('map');
  const { toasts, addToast, removeToast } = useGlobalToast();
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showTour, setShowTour] = useState(() => !localStorage.getItem('titan_tour_done_v2'));
  const dashboard = useDashboard(user?.user_id);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail) dashboard.pinRoadStats(detail);
    };
    window.addEventListener('pin-road-stats', handler);
    return () => window.removeEventListener('pin-road-stats', handler);
  }, [dashboard.pinRoadStats]);

  const handleMapReady = useCallback((_map: mapboxgl.Map) => {
    setMapReady(true);
  }, []);

  // Map utilities hook
  const mapUtilities = useMapUtilities({
    mapRef,
    useRoadTiles: cvRunsHook.useRoadTiles,
    activeCvRunId: cvRunsHook.activeCvRunId,
    cvRoadGeojson,
  });

  // Map layers hook
  const mapLayers = useMapLayers({
    mapRef,
    mapReady,
    mapInitialized,
    setMapInitialized,
    workzoneLines: mapState.workzoneLines,
    layerBehaviorMode: mapState.layerBehaviorMode,
    cvRoadGeojson,
    hasAreaAggregateOverlay: (areaAnalysis.areaAggregateGeojson?.features.length ?? 0) > 0,
    useRoadTiles: cvRunsHook.useRoadTiles,
    roadTileUrlResolved: cvRunsHook.roadTileUrlResolved,
    mapDataset: mapState.mapDataset,
    vehicleData: mapState.vehicleData,
    popupRef,
    clickPopupRef,
    showCvRoadPinnedPanel: () => {}, // Will be set later
  });

  // Map initialization hook
  useMapInitialization({
    useRoadTiles: cvRunsHook.useRoadTiles,
    setCvRoadGeojson,
    setCvRoadLoading,
    setVehicleData: mapState.setVehicleData,
    setMapDataset: mapState.setMapDataset,
    setFilteredData: mapState.setFilteredData,
    setMapOverrideActive: mapState.setMapOverrideActive,
    setLayerBehaviorMode: mapState.setLayerBehaviorMode,
    setWorkzoneLines: mapState.setWorkzoneLines,
    setChartSpecs: chartState.setChartSpecs,
  });

  // State persistence hook
  useStatePersistence({
    chartSpecs: chartState.chartSpecs,
    workzoneLines: mapState.workzoneLines,
    mapDataset: mapState.mapDataset,
  });

  // Map data updates hook (deck.gl overlay)
  useMapDataUpdates({
    mapRef,
    mapReady,
    mapInitialized,
    mapDataset: mapState.mapDataset,
    mapOverrideActive: mapState.mapOverrideActive,
    areaPolygon: areaAnalysis.areaPolygon,
    getMapDataForRender: mapState.getMapDataForRender,
    syncLayerVisibility: mapLayers.syncLayerVisibility,
    rebuildVehicleLayers: mapLayers.rebuildVehicleLayers,
    popupRef,
    clickPopupRef,
  });

  // App handlers hook
  const appHandlers = useAppHandlers({
    vehicleData: mapState.vehicleData,
    mapDataset: mapState.mapDataset,
    filteredData: mapState.filteredData,
    workzoneLines: mapState.workzoneLines,
    mapHistory: mapState.mapHistory,
    mapPrevious: mapState.mapPrevious,
    mapOverrideActive: mapState.mapOverrideActive,
    layerBehaviorMode: mapState.layerBehaviorMode,
    roadAggregateFilter: mapState.roadAggregateFilter,
    areaPolygon: areaAnalysis.areaPolygon,
    cvRoadGeojson,
    useRoadTiles: cvRunsHook.useRoadTiles,
    roadTileDatasetId: (cvRunsHook.activeCvRunId || '').trim(),
    setVehicleData: mapState.setVehicleData,
    setMapDataset: mapState.setMapDataset,
    setFilteredData: mapState.setFilteredData,
    setMapPrevious: mapState.setMapPrevious,
    setMapOverrideActive: mapState.setMapOverrideActive,
    setLayerBehaviorMode: mapState.setLayerBehaviorMode,
    setRoadAggregateFilter: mapState.setRoadAggregateFilter,
    setMapHistory: mapState.setMapHistory,
    setWorkzoneLines: mapState.setWorkzoneLines,
    setCvRoadGeojson,
    setCvRoadLoading,
    setAreaPolygon: () => {}, // areaAnalysis manages its own state
    captureMapHistory: () => mapState.captureMapHistory(areaAnalysis.areaPolygon),
    clearAreaOverlay: areaAnalysis.clearAreaOverlay,
    applySelectionLayerBehavior: mapState.applySelectionLayerBehavior,
    resetMapToRoadNetwork: mapUtilities.resetMapToRoadNetwork,
  });

  // CV run change handler
  const handleActiveCvRunChange = useCallback(async (nextRunId: string) => {
    await (cvRunsHook.handleActiveCvRunChange as any)(nextRunId, {
      onClearData: () => {
        mapState.setVehicleData([]);
        mapState.setMapDataset([]);
        mapState.setFilteredData([]);
      },
      onClearWorkzones: () => {
        if (mapState.workzoneLines.length > 0) {
          mapState.setWorkzoneLines([]);
        }
      },
      onClearArea: areaAnalysis.clearAreaOverlay,
      onResetFilter: () => {
        mapState.setRoadAggregateFilter(null);
        mapState.setMapHistory(null);
        mapState.setMapPrevious(null);
      },
      onResetHistory: () => {
        mapState.setMapHistory(null);
        mapState.setMapPrevious(null);
      },
      onSetOverride: mapState.setMapOverrideActive,
      onSetLayerMode: mapState.setLayerBehaviorMode,
      onLoadRoadAggregates: async () => {
            setCvRoadLoading(true);
            try {
          const roadAgg = await loadCvRoadAggregates();
              setCvRoadGeojson(roadAgg);
            } finally {
              setCvRoadLoading(false);
            }
      },
      onResetMapBounds: mapUtilities.resetMapToRoadNetwork,
      onShowWarning: (message: ExternalChatMessage) => setExternalChatMessage(message),
    });
  }, [cvRunsHook, mapState, areaAnalysis, mapUtilities]);

  // Keyboard shortcuts
  useKeyboardShortcuts({
    mapRef,
    mapStats: mapState.mapStats,
    getMapDataForRender: mapState.getMapDataForRender,
    clearAreaDraw: areaAnalysis.clearAreaDraw,
    captureScreenshot: mapUtilities.captureScreenshot,
    onToggleShortcuts: useCallback(() => setShowShortcuts(prev => !prev), []),
  });


  // applyRoadSegmentFocusFilter is now in useMapLayers hook

  // MapboxDraw setup is now in useAreaAnalysis hook
  // Handle hover coordinate updates
  useMapHoverCoordinates({
    mapRef,
    mapReady,
    setHoverCoord,
  });

  // Polygon layer rendering
  usePolygonLayer({
    mapRef,
    mapReady,
    areaPolygon: areaAnalysis.areaPolygon,
  });

  // Area polygon bbox calculation is now in useAreaAnalysis hook

  // Area summary state (needed for stats panel)
  const [_areaSummary, setAreaSummary] = useState<AreaSummary | null>(null);
  
  // Stats panel hook
  const hasPanelStats = Boolean(_areaSummary && _areaSummary.points > 0);
  const [cvRoadPanelOpen, setCvRoadPanelOpen] = useState(false);
  const {
    showStatsPanel,
    setShowStatsPanel,
    statsPanelPos,
    statsPanelScale,
    statsPanelRef,
    handleStatsDragStart,
    handleStatsDragMove,
    handleStatsDragEnd,
    dockFloatingPanels,
  } = useStatsPanel({ hasPanelStats, cvRoadPanelOpen });
  const { showCvRoadPinnedPanel } = useCvRoadPinnedPanel({
    hasPanelStats,
    showStatsPanel,
    dockFloatingPanels,
    setCvRoadPanelOpen,
    roadColorMode,
  });

  // Map event handlers and layer initialization
  useMapEventHandlers({
    mapRef,
      mapReady,
      mapInitialized,
    areaDrawActive: areaAnalysis.areaDrawActive,
    setMapInitialized,
    mapDataset: mapState.mapDataset,
    vehicleData: mapState.vehicleData,
    useRoadTiles: cvRunsHook.useRoadTiles,
    roadTileUrlResolved: cvRunsHook.roadTileUrlResolved,
    rebuildVehicleLayers: mapLayers.rebuildVehicleLayers,
    showCvRoadPinnedPanel,
    clickPopupRef,
    roadColorMode,
  });

  // Map data updates are now in useMapDataUpdates hook

  // Map error handling
  useMapErrorHandling({
    mapRef,
    mapReady,
  });


  // Road segment focus filter logic
  useRoadSegmentFocusFilter({
    mapReady,
    layerBehaviorMode: mapState.layerBehaviorMode,
    mapOverrideActive: mapState.mapOverrideActive,
    roadAggregateFilter: mapState.roadAggregateFilter,
    mapDataset: mapState.mapDataset,
    vehicleData: mapState.vehicleData,
    workzoneLines: mapState.workzoneLines,
    getMapDataForRender: mapState.getMapDataForRender,
    applyRoadSegmentFocusFilter: mapLayers.applyRoadSegmentFocusFilter,
  });

  // Area drawing functions are in useAreaAnalysis hook
  // Screenshot function is in useMapUtilities hook
  // Keyboard shortcuts are handled by useKeyboardShortcuts hook

  // Workzone lines updates
  useWorkzoneLines({
    mapRef,
    mapReady,
    workzoneLines: mapState.workzoneLines,
    syncLayerVisibility: mapLayers.syncLayerVisibility,
  });

  // CV road lines updates
  useCvRoadLines({
    mapRef,
    mapReady,
    cvRoadGeojson,
    useRoadTiles: cvRunsHook.useRoadTiles,
    syncLayerVisibility: mapLayers.syncLayerVisibility,
    roadColorMode,
  });

  useAreaAggregateRoadLines({
    mapRef,
    mapReady,
    layerBehaviorMode: mapState.layerBehaviorMode,
    areaAggregateGeojson: areaAnalysis.areaAggregateGeojson,
    syncLayerVisibility: mapLayers.syncLayerVisibility,
    roadColorMode,
  });

  // Road bounds fetching
  useRoadBounds({
    mapRef,
    mapReady,
    useRoadTiles: cvRunsHook.useRoadTiles,
    activeCvRunId: cvRunsHook.activeCvRunId,
  });

  // Panel stats computation
  const statsSecondaryLabel = useMemo(() => {
    if (!_areaSummary) return 'CV Points';
    if (_areaSummary.use_hard_brake_secondary) return 'Hard Brake Events';
    if (typeof _areaSummary.vehicles_available === 'boolean') {
      return _areaSummary.vehicles_available ? 'Vehicles' : 'CV Points';
    }
    return (_areaSummary.vehicles ?? 0) > 0 ? 'Vehicles' : 'CV Points';
  }, [_areaSummary]);

  const topRoadUniqueStats = useMemo(() => {
    const features = areaAnalysis.areaAggregateGeojson?.features ?? [];
    type RoadRepresentative = {
      roadName: string;
      segmentId: string;
      uniqueVehicles: number;
      avgUniqueVehiclesPerHour: number | null;
    };
    const topSegmentByRoad = new globalThis.Map<string, RoadRepresentative>();

    const normalizeRoadKey = (value: unknown): string => String(value ?? '')
      .trim()
      .toLowerCase()
      .replace(/\s+/g, ' ');

    const resolveRoadLabel = (props: Record<string, unknown>): string =>
      String(
        props.road_name
          ?? props.roadName
          ?? props.ref
          ?? props.name
          ?? props.label
          ?? 'Unknown road'
      ).trim() || 'Unknown road';

    features.forEach((feature) => {
      const props = (feature.properties ?? {}) as Record<string, unknown>;
      const roadName = resolveRoadLabel(props);
      const roadKey = normalizeRoadKey(roadName);
      if (!roadKey) return;
      const segmentIdRaw =
        props.road_segment_id ??
        props.segment_id ??
        props.roadSegmentId ??
        props.way_id ??
        props.wayId;
      const segmentId = String(segmentIdRaw ?? '').trim();
      const uniqueRaw = Number(props.unique_vehicles_total ?? 0);
      const uniqueVehicles = Number.isFinite(uniqueRaw) ? Math.max(0, uniqueRaw) : 0;
      if (uniqueVehicles <= 0) return;
      const avgFromHourly = computeAvgUniqueVehiclesPerHourFromHourlyJson(props.hourly_unique_vehicles_json);
      const avgRaw = Number(props.avg_unique_vehicles_per_hour);
      const avgUniqueVehiclesPerHour =
        avgFromHourly ?? (Number.isFinite(avgRaw) ? avgRaw : null);

      const existing = topSegmentByRoad.get(roadKey);
      if (existing && existing.uniqueVehicles >= uniqueVehicles) return;
      topSegmentByRoad.set(roadKey, {
        roadName,
        segmentId,
        uniqueVehicles,
        avgUniqueVehiclesPerHour,
      });
    });

    let bestRoadName = 'No data';
    let bestUniqueVehicles = 0;
    let bestAvgUniqueVehiclesPerHour: number | null = null;
    topSegmentByRoad.forEach((segment: RoadRepresentative) => {
      if (segment.uniqueVehicles <= bestUniqueVehicles) return;
      bestUniqueVehicles = segment.uniqueVehicles;
      const normalizedSegmentId = String(segment.segmentId || '').trim().toLowerCase();
      const hasSegmentId =
        normalizedSegmentId.length > 0 &&
        !['unknown', 'n/a', 'null', 'none'].includes(normalizedSegmentId);
      bestRoadName = hasSegmentId
        ? `${segment.roadName} (${segment.segmentId})`
        : segment.roadName;
      bestAvgUniqueVehiclesPerHour = segment.avgUniqueVehiclesPerHour;
    });

    return {
      roadName: bestRoadName,
      uniqueVehicles: bestUniqueVehicles,
      avgUniqueVehiclesPerHour: bestAvgUniqueVehiclesPerHour,
    };
  }, [areaAnalysis.areaAggregateGeojson]);

  const panelStats = useMemo(() => {
    if (_areaSummary && _areaSummary.points > 0) {
      const secondaryCount = _areaSummary.use_hard_brake_secondary
        ? (_areaSummary.hard_brakes ?? 0)
        : (statsSecondaryLabel === 'Vehicles'
          ? (_areaSummary.vehicles ?? 0)
          : (_areaSummary.points ?? 0));
      return {
        total: _areaSummary.points,
        cvPoints: secondaryCount,
        topRoadName: topRoadUniqueStats.roadName,
        topRoadUniqueVehicles: topRoadUniqueStats.uniqueVehicles,
        topRoadAvgUniqueVehiclesPerHour: topRoadUniqueStats.avgUniqueVehiclesPerHour,
        crashes: _areaSummary.crashes ?? 0,
        hardBraking: _areaSummary.hard_brakes ?? 0,
        avgSpeed: _areaSummary.avg_speed ? Math.round(_areaSummary.avg_speed) : 0,
        maxSpeed: _areaSummary.max_speed ? Math.round(_areaSummary.max_speed) : 0,
      };
    }
    return {
      total: 0,
      cvPoints: 0,
      topRoadName: 'No data',
      topRoadUniqueVehicles: 0,
      topRoadAvgUniqueVehiclesPerHour: null,
      crashes: 0,
      hardBraking: 0,
      avgSpeed: 0,
      maxSpeed: 0,
    };
  }, [_areaSummary, statsSecondaryLabel, topRoadUniqueStats]);

  const statsBboxLabel = useMemo(() => {
    if (!areaAnalysis.areaBBox) return null;
    return `SW ${areaAnalysis.areaBBox.minLon.toFixed(4)}, ${areaAnalysis.areaBBox.minLat.toFixed(4)} | NE ${areaAnalysis.areaBBox.maxLon.toFixed(4)}, ${areaAnalysis.areaBBox.maxLat.toFixed(4)}`;
  }, [areaAnalysis.areaBBox]);

  const handleGeneratedChartBarClick = useCallback((
    _chart: { id: string; title: string; payload: GeneratedChartPayload; timestamp: Date },
    context: ChartBarClickContext
  ) => {
    void _chart;
    const payload = context.payload;
    if (payload.type !== 'bar') return;
    if (payload.meta?.chartRole !== 'area_top_unique_segments') return;
    const map = mapRef.current;
    if (!map) return;
    const features = areaAnalysis.areaAggregateGeojson?.features ?? [];
    if (!features.length) {
      addToast('No polygon road geometry available to zoom to.', 'warning');
      return;
    }

    const segmentIdRaw = context.target?.road_segment_id;
    const targetSegmentId = String(segmentIdRaw ?? '').trim().toLowerCase();
    const roadNameRaw = context.target?.road_name ?? context.label;
    const targetRoadName = String(roadNameRaw ?? '').trim().toLowerCase();

    const matchedFeatures = features.filter((feature) => {
      const props = (feature.properties ?? {}) as Record<string, unknown>;
      const segmentCandidate = String(
        props.road_segment_id ??
        props.segment_id ??
        props.roadSegmentId ??
        props.way_id ??
        props.wayId ??
        ''
      ).trim().toLowerCase();
      if (targetSegmentId && segmentCandidate && segmentCandidate === targetSegmentId) {
        return true;
      }
      if (!targetRoadName) return false;
      const roadCandidate = String(
        props.road_name ??
        props.roadName ??
        props.label ??
        props.ref ??
        props.name ??
        ''
      ).trim().toLowerCase();
      return Boolean(roadCandidate && roadCandidate === targetRoadName);
    });

    if (!matchedFeatures.length) {
      addToast(`Could not find map geometry for ${context.label}.`, 'warning');
      return;
    }

    let minLon = Number.POSITIVE_INFINITY;
    let minLat = Number.POSITIVE_INFINITY;
    let maxLon = Number.NEGATIVE_INFINITY;
    let maxLat = Number.NEGATIVE_INFINITY;
    let coordCount = 0;
    const extendBounds = (coord: unknown) => {
      if (!Array.isArray(coord) || coord.length < 2) return;
      const lon = Number(coord[0]);
      const lat = Number(coord[1]);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
      minLon = Math.min(minLon, lon);
      minLat = Math.min(minLat, lat);
      maxLon = Math.max(maxLon, lon);
      maxLat = Math.max(maxLat, lat);
      coordCount += 1;
    };

    matchedFeatures.forEach((feature) => {
      const geom = feature.geometry;
      if (!geom) return;
      if (geom.type === 'LineString') {
        geom.coordinates.forEach((coord) => extendBounds(coord));
      } else if (geom.type === 'MultiLineString') {
        geom.coordinates.forEach((line) => line.forEach((coord) => extendBounds(coord)));
      }
    });

    if (coordCount === 0) {
      addToast(`No valid coordinates found for ${context.label}.`, 'warning');
      return;
    }

    setActiveView('map');
    mapState.setLayerBehaviorMode('road-network');
    map.fitBounds(
      [[minLon, minLat], [maxLon, maxLat]],
      { padding: 36, maxZoom: 18, duration: 900 }
    );
  }, [addToast, areaAnalysis.areaAggregateGeojson, mapState]);

  // Confirm area handler
  const confirmArea = useCallback(async () => {
    await areaAnalysis.confirmArea({
      onPushMessage: (role, content) => {
        setExternalChatMessage({
          id: `ext-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          role,
          content,
        });
      },
      onSetSummary: (summary) => {
        setAreaSummary(summary);
        if ((summary?.points ?? 0) > 0) {
          setShowStatsPanel(true);
        }
      },
      onSetWorkzones: (lines) => mapState.setWorkzoneLines(lines),
      onSetPoints: (points) => {
        mapState.setMapPrevious(mapState.mapDataset);
        mapState.setFilteredData(points);
        mapState.setMapDataset(points);
        mapState.setMapOverrideActive(true);
      },
      onSetOverride: mapState.setMapOverrideActive,
      onSetLayerMode: mapState.setLayerBehaviorMode,
      onCaptureHistory: () => mapState.captureMapHistory(areaAnalysis.areaPolygon),
      onSetPrevious: mapState.setMapPrevious,
      onSetFiltered: mapState.setFilteredData,
      onSetDataset: mapState.setMapDataset,
      onHandleChartPayload: chartState.handleChartPayload,
    });
  }, [areaAnalysis, mapState, chartState]);

  // Handlers are now provided by useAppHandlers and useChartState hooks
  return (
  <div className="app">
    <AppTopBar
      activeCvRunId={cvRunsHook.activeCvRunId}
      cvRunLoading={cvRunsHook.cvRunLoading}
      cvRunSwitching={cvRunsHook.cvRunSwitching}
      cvRunError={cvRunsHook.cvRunError}
      cvRunOptions={cvRunsHook.cvRunOptions}
      canUndo={Boolean(mapState.mapHistory || (mapState.mapPrevious && mapState.mapPrevious.length > 0))}
      areaPolygonReady={Boolean(areaAnalysis.areaPolygon)}
      areaDrawActive={areaAnalysis.areaDrawActive}
      areaAnalysisLoading={areaAnalysis.areaAnalysisLoading}
      activeView={activeView}
      roadCount={cvRoadGeojson?.features?.length ?? 0}
      roadColorMode={roadColorMode}
      onRoadColorModeChange={setRoadColorMode}
      onViewChange={setActiveView}
      onCvRunChange={handleActiveCvRunChange}
      onResetMap={appHandlers.handleResetMap}
      onUndoMap={appHandlers.handleUndoMap}
      onStartAreaDraw={areaAnalysis.startAreaDraw}
      onConfirmArea={confirmArea}
      onClearAreaDraw={areaAnalysis.clearAreaDraw}
      onOpenIngestion={() => setShowIngestion(true)}
      onOpenInfo={() => setShowInfo(true)}
      onStartTour={() => { localStorage.removeItem('titan_tour_done_v2'); setShowTour(true); }}
      onScreenshot={() => { mapUtilities.captureScreenshot(); addToast('Screenshot saved', 'success'); }}
      onToggleShortcuts={() => setShowShortcuts(prev => !prev)}
      userName={user?.name || user?.email || ''}
      userEmail={user?.email}
      avatarUrl={user?.avatar_url}
      userProvider={user?.provider}
      userCreatedAt={user?.created_at}
      onLogout={logout}
    />

    {/* Map with legend — hidden (not unmounted) when dashboard is active */}
    <div style={{ display: activeView === 'map' ? 'contents' : 'none' }}>
      <Map mapRef={mapRef} onMapReady={handleMapReady} />

      <MapLegend cvRoadLoading={cvRoadLoading} hoverCoord={hoverCoord} areaBBox={areaAnalysis.areaBBox} mapRef={mapRef} roadColorMode={roadColorMode} />

      <MapSearchBar mapRef={mapRef} />

      {/* Map Overlays */}

      {/* Draggable Statistics Panel */}
      <StatsOverlay
        open={hasPanelStats && showStatsPanel}
        statsPanelRef={statsPanelRef}
        statsPanelPos={statsPanelPos}
        statsPanelScale={statsPanelScale}
        panelStats={panelStats}
        statsBboxLabel={statsBboxLabel}
        secondaryLabel={statsSecondaryLabel}
        onClose={() => setShowStatsPanel(false)}
        onDragStart={handleStatsDragStart}
        onDragMove={handleStatsDragMove}
        onDragEnd={handleStatsDragEnd}
      />
    </div>

    {/* Dashboard view */}
    {activeView === 'dashboard' && (
      <div className="dashboard-wrapper">
        <DashboardView
          widgets={dashboard.widgets}
          dashboards={dashboard.dashboards}
          activeDashboardId={dashboard.activeDashboardId}
          activeDashboardName={dashboard.activeDashboardName}
          autoRefresh={dashboard.autoRefresh}
          refreshTick={dashboard.refreshTick}
          fullscreenWidgetId={dashboard.fullscreenWidgetId}
          onRemoveWidget={dashboard.removeWidget}
          onAddDataQuality={dashboard.addDataQualityWidget}
          onAddStatSummary={dashboard.addStatSummaryWidget}
          onClearDashboard={dashboard.clearDashboard}
          onLayoutChange={dashboard.updateLayouts}
          onUpdateTitle={dashboard.updateWidgetTitle}
          onToggleLock={dashboard.toggleWidgetLock}
          onSwitchDashboard={dashboard.switchDashboard}
          onCreateDashboard={dashboard.createDashboard}
          onRenameDashboard={dashboard.renameDashboard}
          onDeleteDashboard={dashboard.deleteDashboard}
          onAddAnalyticsWidget={dashboard.addAnalyticsWidget}
          onAddAllAnalytics={dashboard.addAllAnalyticsWidgets}
          onApplyTemplate={dashboard.applyTemplate}
          onToggleAutoRefresh={dashboard.toggleAutoRefresh}
          onSetFullscreenWidget={dashboard.setFullscreenWidgetId}
          onDuplicateDashboard={dashboard.duplicateDashboard}
          onSortWidgets={dashboard.sortWidgetsByType}
          onToggleCollapse={dashboard.toggleWidgetCollapse}
          onUndoRemove={dashboard.undoRemoveWidget}
          onExportDashboard={dashboard.exportDashboard}
          onImportDashboard={dashboard.importDashboard}
          onSetWidgetColorTag={dashboard.setWidgetColorTag}
          onSetWidgetNote={dashboard.setWidgetNote}
          onSetWidgetSize={dashboard.setWidgetSize}
          onMoveWidgetToDashboard={dashboard.moveWidgetToDashboard}
        />
      </div>
    )}

    {/* Sidebar panel */}
    <VisualizationPanel
      visualizations={chartState.visualizations}
      onRemove={(id) => chartState.setVisualizations(prev => prev.filter(v => v.id !== id))}
      onClear={() => {
        chartState.setVisualizations([]);
        clearAppState();
      }}
      generatedCharts={chartState.chartSpecs}
      onRemoveGenerated={(id) => chartState.setChartSpecs(prev => prev.filter(c => c.id !== id))}
      onClearGenerated={() => {
        chartState.setChartSpecs([]);
        clearAppState();
      }}
      onGenerateCharts={chartState.handleChartPayload}
      onReplaceGenerated={chartState.handleReplaceChart}
      onPinToDashboard={(title, payload) => { dashboard.pinChart(title, payload); addToast('Chart pinned to dashboard', 'success'); }}
      onGeneratedChartBarClick={handleGeneratedChartBarClick}
      chartEditingEnabled={chartEditingEnabled}
      chartEditingDisabledReason={chartEditingDisabledReason}
    />

    {/* Chat interface */}
    <MinimalChat
      userId={user?.user_id}
      onFilter={appHandlers.handleFilter}
      onVisualize={appHandlers.handleVisualize}
      vehicleData={mapState.vehicleData}
      onShowChart={(type) => chartState.handleShowChart(type, mapState.filteredData, mapState.vehicleData)}
      onServerSelection={appHandlers.handleServerSelection}
      onWorkzoneLines={appHandlers.handleWorkzoneSelection}
      onRoadAggregateFilter={appHandlers.handleRoadAggregateFilter}
      onChartPayload={(payloads: unknown[]) => chartState.handleChartPayload(payloads as any)}
      onOpenIngestion={() => setShowIngestion(true)}
      onClearHistory={appHandlers.handleClearHistoryView}
      externalMessage={externalChatMessage}
    />

    {/* Chart overlay modal */}
    {chartState.showChart && (
      <ChartOverlay
        data={mapState.filteredData.length > 0 ? mapState.filteredData : mapState.vehicleData}
        type={chartState.showChart.type}
        onClose={() => chartState.setShowChart(null)}
      />
    )}

    <InfoOverlay open={showInfo} onClose={() => setShowInfo(false)} />
    <IngestionOverlay open={showIngestion} onClose={() => setShowIngestion(false)} />
    <KeyboardShortcutsOverlay open={showShortcuts} onClose={() => setShowShortcuts(false)} />
    {showTour && <GuidedTour onComplete={() => setShowTour(false)} />}
    <ToastContainer toasts={toasts} onRemove={removeToast} />
  </div>
);
}

export default App;

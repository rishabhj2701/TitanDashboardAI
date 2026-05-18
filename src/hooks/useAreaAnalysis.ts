import { useState, useCallback, useEffect, useRef } from 'react';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
// import type mapboxgl from 'mapbox-gl';
import {
  analyzeArea,
  type AnalysisResponsePayload,
} from '../api/analysisClient';
import type { GeneratedChartPayload } from '../types/charts';
import type { AreaSummary } from '../features/map/types';

export type AreaAggregateGeoJSON = GeoJSON.FeatureCollection<
  GeoJSON.LineString | GeoJSON.MultiLineString,
  Record<string, any>
>;

export interface UseAreaAnalysisResult {
  areaPolygon: GeoJSON.Polygon | null;
  areaAggregateGeojson: AreaAggregateGeoJSON | null;
  areaDrawActive: boolean;
  areaAnalysisLoading: boolean;
  areaSummary: AreaSummary | null;
  areaBBox: { minLon: number; minLat: number; maxLon: number; maxLat: number } | null;
  drawRef: React.MutableRefObject<InstanceType<typeof MapboxDraw> | null>;
  startAreaDraw: () => void;
  clearAreaDraw: () => void;
  confirmArea: (options: ConfirmAreaCallbacks) => Promise<void>;
  clearAreaOverlay: () => void;
}

type ConfirmAreaCallbacks = {
  onPushMessage: (role: 'user' | 'assistant', content: string) => void;
  onSetSummary: (summary: AreaSummary) => void;
  onSetWorkzones: (lines: any[]) => void;
  onSetPoints: (points: any[]) => void;
  onSetOverride: (override: boolean) => void;
  onSetLayerMode: (mode: 'road-network' | 'focus-selection' | 'mixed') => void;
  onCaptureHistory: () => void;
  onSetPrevious: (data: any[]) => void;
  onSetFiltered: (data: any[]) => void;
  onSetDataset: (data: any[]) => void;
  onHandleChartPayload: (charts: any[]) => void;
};

const normalizeRoadKey = (value: unknown): string => {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
};

const resolveRoadLabel = (props: Record<string, unknown>): string => {
  return String(
    props.road_name
      ?? props.roadName
      ?? props.ref
      ?? props.name
      ?? props.label
      ?? '[unknown road]'
  ).trim() || '[unknown road]';
};

const buildRoadCountLookup = (rows: unknown): Map<string, number> => {
  const lookup = new Map<string, number>();
  if (!Array.isArray(rows)) return lookup;
  rows.forEach((row: any) => {
    const key = normalizeRoadKey(row?.road_name ?? row?.roadName);
    if (!key) return;
    const count = Number(row?.count ?? 0);
    lookup.set(key, Number.isFinite(count) ? count : 0);
  });
  return lookup;
};

export const useAreaAnalysis = (
  mapRef: React.RefObject<mapboxgl.Map | null>,
  mapReady: boolean,
  areaAnalysisTimeoutMs: number = 300_000,
  activeCvRunId?: string
): UseAreaAnalysisResult => {
  const [areaPolygon, setAreaPolygon] = useState<GeoJSON.Polygon | null>(null);
  const [areaAggregateGeojson, setAreaAggregateGeojson] = useState<AreaAggregateGeoJSON | null>(null);
  const [areaDrawActive, setAreaDrawActive] = useState(false);
  const [areaAnalysisLoading, setAreaAnalysisLoading] = useState(false);
  const [areaSummary, setAreaSummary] = useState<AreaSummary | null>(null);
  const [areaBBox, setAreaBBox] = useState<{ minLon: number; minLat: number; maxLon: number; maxLat: number } | null>(null);
  const drawRef = useRef<InstanceType<typeof MapboxDraw> | null>(null);
  const areaDrawActiveRef = useRef(false);
  const preservePolygonOnDeleteRef = useRef(false);

  useEffect(() => {
    areaDrawActiveRef.current = areaDrawActive;
  }, [areaDrawActive]);

  const clearAreaOverlay = useCallback(() => {
    try {
      if (drawRef.current && mapRef.current) {
        preservePolygonOnDeleteRef.current = false;
        drawRef.current.deleteAll();
        drawRef.current.changeMode('simple_select');
      }
    } catch (err) {
      // Ignore draw errors if map not ready
      console.debug('clearAreaOverlay skipped:', err);
    }
    setAreaPolygon(null);
    setAreaAggregateGeojson(null);
    areaDrawActiveRef.current = false;
    setAreaDrawActive(false);
    setAreaSummary(null);
    window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: false } }));
  }, [mapRef]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    if (drawRef.current) return;

    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: {},
      defaultMode: 'simple_select',
    });

    const handleDrawUpdate = () => {
      const data = draw.getAll();
      const polygon = data.features.find((feature: GeoJSON.Feature) => feature.geometry?.type === 'Polygon');
      if (polygon && polygon.geometry.type === 'Polygon') {
        setAreaPolygon(polygon.geometry as GeoJSON.Polygon);
        if (areaDrawActiveRef.current) {
          areaDrawActiveRef.current = false;
          setAreaDrawActive(false);
          window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: false } }));
          preservePolygonOnDeleteRef.current = true;
          setTimeout(() => {
            try {
              draw.deleteAll();
              draw.changeMode('simple_select');
            } catch (err) {
              console.debug('draw freeze error:', err);
            }
          }, 0);
        }
      } else {
        if (preservePolygonOnDeleteRef.current) {
          preservePolygonOnDeleteRef.current = false;
          return;
        }
        setAreaPolygon(null);
        window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: false } }));
      }
    };

    map.addControl(draw);
    drawRef.current = draw;
    map.on('draw.create', handleDrawUpdate);
    map.on('draw.update', handleDrawUpdate);
    map.on('draw.delete', handleDrawUpdate);

    return () => {
      try {
        // Check if map is still valid before calling methods
        if (map && typeof map.off === 'function') {
          map.off('draw.create', handleDrawUpdate);
          map.off('draw.update', handleDrawUpdate);
          map.off('draw.delete', handleDrawUpdate);
        }
        if (drawRef.current && map && typeof map.removeControl === 'function') {
          map.removeControl(drawRef.current);
        }
      } catch (err) {
        console.debug('[Map] Cleanup skipped (map destroyed):', err);
      }
      drawRef.current = null;
    };
  }, [mapReady, mapRef]);

  useEffect(() => {
    if (!areaPolygon) {
      setAreaBBox(null);
      return;
    }
    let minLon = Infinity;
    let minLat = Infinity;
    let maxLon = -Infinity;
    let maxLat = -Infinity;
    areaPolygon.coordinates.forEach((ring) => {
      ring.forEach(([lon, lat]) => {
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
        minLon = Math.min(minLon, lon);
        minLat = Math.min(minLat, lat);
        maxLon = Math.max(maxLon, lon);
        maxLat = Math.max(maxLat, lat);
      });
    });
    if (minLon === Infinity) {
      setAreaBBox(null);
      return;
    }
    setAreaBBox({ minLon, minLat, maxLon, maxLat });
  }, [areaPolygon]);

  const startAreaDraw = useCallback(() => {
    if (!drawRef.current || !mapRef.current) return;
    if (areaPolygon && !areaDrawActive) {
      console.info('[Area] Polygon already exists; clear it before drawing a new one.');
      return;
    }
    try {
      window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: true } }));
      preservePolygonOnDeleteRef.current = false;
      drawRef.current.deleteAll();
      setAreaPolygon(null);
      setAreaAggregateGeojson(null);
      setAreaSummary(null);
      areaDrawActiveRef.current = true;
      setAreaDrawActive(true);
      drawRef.current.changeMode('draw_polygon');
      window.dispatchEvent(new CustomEvent('map-hover-clear'));
    } catch (err) {
      console.debug('startAreaDraw error:', err);
    }
  }, [mapRef, areaPolygon, areaDrawActive]);

  const clearAreaDraw = useCallback(() => {
    try {
      if (drawRef.current && mapRef.current) {
        preservePolygonOnDeleteRef.current = false;
        drawRef.current.deleteAll();
        drawRef.current.changeMode('simple_select');
      }
    } catch (err) {
      console.debug('clearAreaDraw error:', err);
    }
    setAreaPolygon(null);
    setAreaAggregateGeojson(null);
    areaDrawActiveRef.current = false;
    setAreaDrawActive(false);
    setAreaSummary(null);
    window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: false } }));
    window.dispatchEvent(new CustomEvent('map-hover-clear'));
  }, [mapRef]);

  const normalizeAreaAggregateGeojson = useCallback((value: unknown): AreaAggregateGeoJSON | null => {
    if (!value || typeof value !== 'object') return null;
    const fc = value as { type?: string; features?: any[] };
    if (fc.type !== 'FeatureCollection' || !Array.isArray(fc.features)) return null;
    const features = fc.features.filter((feature) => {
      const geomType = feature?.geometry?.type;
      return geomType === 'LineString' || geomType === 'MultiLineString';
    });
    return {
      type: 'FeatureCollection',
      features,
    };
  }, []);

  const applyAreaAnalysisPayload = useCallback((data: AnalysisResponsePayload, options: ConfirmAreaCallbacks) => {
    const mapSelection = data?.mapSelection;
    const summary = data?.summary as AreaSummary | undefined;
    const responseText = data?.response as string | undefined;
    const mode = data?.mode ?? 'detail';
    const areaAggregateGeojsonRaw = data?.areaAggregate?.geojson;
    const normalizedAreaAggregate = normalizeAreaAggregateGeojson(areaAggregateGeojsonRaw);
    const crashByRoadSummary = (summary as any)?.crash_by_road;
    const hardBrakeByRoadSummary = (summary as any)?.hard_brake_by_road;
    const crashCountLookup = buildRoadCountLookup(crashByRoadSummary);
    const hardBrakeCountLookup = buildRoadCountLookup(hardBrakeByRoadSummary);

    const areaAggregateWithCounts = normalizedAreaAggregate
      ? {
          type: 'FeatureCollection' as const,
          features: normalizedAreaAggregate.features.map((feature) => {
            const props = (feature.properties ?? {}) as Record<string, any>;
            const crashCountRaw = props.crash_count ?? props.crashes;
            const hardBrakeCountRaw = props.hard_brake_count ?? props.hard_brakes;
            const crashCountNum = Number(crashCountRaw);
            const hardBrakeCountNum = Number(hardBrakeCountRaw);
            const hasCrashCount = Number.isFinite(crashCountNum);
            const hasHardBrakeCount = Number.isFinite(hardBrakeCountNum);
            const roadKey = normalizeRoadKey(
              props.road_name ?? props.roadName ?? props.name ?? props.label ?? props.ref
            );
            const fallbackCrashCount = roadKey ? (crashCountLookup.get(roadKey) ?? 0) : 0;
            const fallbackHardBrakeCount = roadKey ? (hardBrakeCountLookup.get(roadKey) ?? 0) : 0;
            return {
              ...feature,
              properties: {
                ...props,
                area_analysis: true,
                crash_count: hasCrashCount ? Math.max(0, Math.round(crashCountNum)) : fallbackCrashCount,
                hard_brake_count: hasHardBrakeCount ? Math.max(0, Math.round(hardBrakeCountNum)) : fallbackHardBrakeCount,
              },
            };
          }),
        }
      : null;
    const useAggregateMode = mode === 'aggregate' || ((areaAggregateWithCounts?.features.length ?? 0) > 0);

    if (summary) {
      setAreaSummary(summary);
      options.onSetSummary(summary);
      const charts: GeneratedChartPayload[] = [];

      const roadAvg = (summary as any)?.road_avg_speeds;
      if (Array.isArray(roadAvg) && roadAvg.length > 0) {
        charts.push({
          type: 'bar',
          title: 'Avg Speed by Road (Polygon)',
          xLabel: 'Avg speed (mph)',
          yLabel: 'Road',
          xValues: roadAvg.map((row: any) => row.road_name || 'Unknown'),
          series: [
            {
              label: 'Avg speed',
              values: roadAvg.map((row: any) => row.avg_speed ?? null),
            },
          ],
          orientation: 'horizontal',
        });
      }

      if (Array.isArray(crashByRoadSummary) && crashByRoadSummary.length > 0) {
        charts.push({
          type: 'bar',
          title: 'Crashes by Road (Polygon)',
          xLabel: 'Crash events',
          yLabel: 'Road',
          xValues: crashByRoadSummary.map((row: any) => row.road_name || '[unknown road]'),
          series: [
            {
              label: 'Crashes',
              values: crashByRoadSummary.map((row: any) => Number(row.count ?? 0)),
            },
          ],
          orientation: 'horizontal',
        });
      }

      if (Array.isArray(hardBrakeByRoadSummary) && hardBrakeByRoadSummary.length > 0) {
        charts.push({
          type: 'bar',
          title: 'Hard Braking by Road (Polygon)',
          xLabel: 'Hard-brake events',
          yLabel: 'Road',
          xValues: hardBrakeByRoadSummary.map((row: any) => row.road_name || '[unknown road]'),
          series: [
            {
              label: 'Hard braking',
              values: hardBrakeByRoadSummary.map((row: any) => Number(row.count ?? 0)),
            },
          ],
          orientation: 'horizontal',
        });
      }

      const topSegmentByRoad = new Map<string, {
        segmentLabel: string;
        segmentId: string | null;
        roadName: string;
        uniqueVehicles: number;
      }>();
      (areaAggregateWithCounts?.features ?? []).forEach((feature) => {
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
        const hasSegmentId = Boolean(segmentId) && !['unknown', 'n/a', 'null', 'none'].includes(segmentId.toLowerCase());
        const segmentLabel = hasSegmentId ? `${roadName} (#${segmentId})` : roadName;
        const existing = topSegmentByRoad.get(roadKey);
        if (!existing || uniqueVehicles > existing.uniqueVehicles) {
          topSegmentByRoad.set(roadKey, {
            segmentLabel,
            segmentId: hasSegmentId ? segmentId : null,
            roadName,
            uniqueVehicles,
          });
        }
      });
      const topSegmentsByUniqueRows = Array.from(topSegmentByRoad.values())
        .sort((a, b) => b.uniqueVehicles - a.uniqueVehicles)
        .slice(0, 10);

      if (topSegmentsByUniqueRows.length > 0) {
        charts.push({
          type: 'bar',
          title: 'Top 10 Roads by Unique Vehicles (Top Segment per Road)',
          xLabel: 'Unique vehicles',
          yLabel: 'Road (segment)',
          xValues: topSegmentsByUniqueRows.map((row) => row.segmentLabel),
          categoryTargets: topSegmentsByUniqueRows.map((row) => ({
            road_segment_id: row.segmentId,
            road_name: row.roadName,
          })),
          meta: {
            chartRole: 'area_top_unique_segments',
          },
          series: [
            {
              label: 'Unique vehicles',
              values: topSegmentsByUniqueRows.map((row) => row.uniqueVehicles),
            },
          ],
          orientation: 'horizontal',
        });
      }

      if (charts.length > 0) {
        options.onHandleChartPayload(charts);
      }
    }

    if (Array.isArray(mapSelection?.lines)) {
      const lineSegments = mapSelection.lines.map((line: any, idx: number) => ({
        id: String(line.id ?? `workzone-line-${idx}`),
        coordinates: Array.isArray(line.coordinates)
          ? line.coordinates.map((pair: any) => [Number(pair[0]), Number(pair[1])])
          : [],
        roadName: line.roadName ?? 'Unknown',
        roadSegmentId: line.roadSegmentId ?? line.road_segment_id,
        datasetId: line.datasetId ?? line.dataset_id,
        status: line.status,
        description: line.description,
        startDate: line.startDate,
        endDate: line.endDate,
        exclusive: line.exclusive !== false,
      }));
      options.onSetWorkzones(lineSegments);
    } else {
      options.onSetWorkzones([]);
    }

    const serverPoints = mapSelection?.points;
    console.log('[Area] mapSelection points:', Array.isArray(serverPoints) ? serverPoints.length : 'none');
    let mappedPoints: any[] = [];
    if (Array.isArray(serverPoints)) {
      const bbox = areaBBox;
      const inBbox = (lon: number, lat: number) =>
        !bbox
          ? true
          : lon >= bbox.minLon &&
            lon <= bbox.maxLon &&
            lat >= bbox.minLat &&
            lat <= bbox.maxLat;
      let swapped = 0;
      let outOfBbox = 0;
      mappedPoints = serverPoints.map((p: any, i: number) => {
        let longitude = Number(p.longitude ?? p.lon);
        let latitude = Number(p.latitude ?? p.lat);
        if (bbox && !inBbox(longitude, latitude) && inBbox(latitude, longitude)) {
          const tmp = longitude;
          longitude = latitude;
          latitude = tmp;
          swapped += 1;
        }
        if (bbox && !inBbox(longitude, latitude)) {
          outOfBbox += 1;
        }
        const speedLimitRaw = p.SpeedLimitMPH ?? p.speedLimit ?? p.speed_limit ?? p.speed_limit_mph ?? p.speedlimit_mph;
        const speedLimitNum = speedLimitRaw == null ? 0 : Number(speedLimitRaw);
        const speedLimitKnown = Number.isFinite(speedLimitNum) && speedLimitNum > 0;
        return {
          id: String(p.id ?? `area_${i}`),
          type: p.type ?? 'Vehicle',
          longitude,
          latitude,
          timestamp: p.timestamp ?? p.ts ?? '',
          speed: Number(p.speed ?? 0),
          bearing: Number(p.bearing ?? 0),
          acceleration: {
            x: Number(p.acceleration?.x ?? p.acc_x ?? p.AccX ?? p.accX ?? 0),
            y: Number(p.acceleration?.y ?? p.acc_y ?? p.AccY ?? p.accY ?? 0),
          },
          speedLimit: speedLimitKnown ? speedLimitNum : 0,
          speedLimitKnown,
          roadName: p.road_name ?? p.roadName ?? p.road ?? '',
          eventType: p.eventType ?? p.type,
          eventDate: p.eventDate ?? p.timestamp ?? '',
          decelerationG: typeof p.decelerationG === 'number' ? p.decelerationG : Number(p.decelerationG ?? NaN),
          speedOverLimit: typeof p.speed_over_limit === 'number' ? p.speed_over_limit : Number(p.speedOverLimit ?? NaN),
          severity: p.severity,
          crashSeverity: p.crashSeverity ?? p.severity,
          accident_date: p.accident_date ?? p.accidentDate ?? null,
          accident_time: p.accident_time ?? p.accidentTime ?? null,
          hp_acc_image_no: p.hp_acc_image_no ?? p.hpAccImageNo ?? null,
          primary_id: p.primary_id ?? p.primaryId ?? null,
          road_segment_id:
            p.road_segment_id ??
            p.segment_id ??
            p.roadSegmentId ??
            p.way_id ??
            p.wayId ??
            undefined,
        };
      }).filter((p) =>
        Number.isFinite(p.longitude) &&
        Number.isFinite(p.latitude) &&
        Math.abs(p.longitude) <= 180 &&
        Math.abs(p.latitude) <= 90
      );
      if (swapped > 0) {
        console.warn(`[Area] swapped lat/lon for ${swapped} points based on polygon bbox`);
      }
      if (bbox && outOfBbox > 0) {
        console.warn(`[Area] ${outOfBbox} points fall outside polygon bbox`);
      }
    }

    if (useAggregateMode) {
      setAreaAggregateGeojson(areaAggregateWithCounts);
      options.onCaptureHistory();
      options.onSetPrevious([]);
      options.onSetFiltered(mappedPoints);
      options.onSetDataset(mappedPoints);
      options.onSetOverride(true);
      options.onSetLayerMode('road-network');
      if (!areaAggregateWithCounts || areaAggregateWithCounts.features.length === 0) {
        options.onPushMessage('assistant', 'Area aggregate returned no roads for this polygon.');
      }
      if (Array.isArray(serverPoints) && serverPoints.length === 0) {
        options.onPushMessage('assistant', 'No crash/hard-brake points returned inside the polygon for aggregate mode.');
      }
    } else {
      setAreaAggregateGeojson(null);
      options.onSetLayerMode('focus-selection');

      if (Array.isArray(serverPoints)) {
        if (mappedPoints.length === 0) {
          console.warn('[Area] No valid lat/lon points in polygon selection.');
        }
        options.onCaptureHistory();
        options.onSetPrevious([]);
        options.onSetFiltered(mappedPoints);
        options.onSetDataset(mappedPoints);
        options.onSetOverride(true);
        if (serverPoints.length === 0) {
          options.onPushMessage('assistant', 'No points returned inside the polygon. Try a larger area or confirm the dataset has points there.');
        }
      } else {
        options.onPushMessage('assistant', 'Polygon analysis did not return point data; keeping current points on the map.');
        options.onSetOverride(false);
      }
    }
    if (responseText) {
      options.onPushMessage('assistant', responseText);
    }
  }, [areaBBox, normalizeAreaAggregateGeojson]);

  const confirmArea = useCallback(async (options: ConfirmAreaCallbacks) => {
    if (!areaPolygon) return;
    setAreaAnalysisLoading(true);
    window.dispatchEvent(new CustomEvent('map-hover-clear'));
    options.onPushMessage('user', 'Analyze the selected polygon area.');
    const serverTimeoutMs = Math.max(5_000, Number(areaAnalysisTimeoutMs || 300_000));
    try {
      const cvDatasetId = (activeCvRunId || '').trim() || undefined;
      const data: AnalysisResponsePayload = await analyzeArea(
        { polygon: areaPolygon, analysis_mode: 'aggregate', cv_dataset_id: cvDatasetId },
        { timeoutMs: serverTimeoutMs + 10_000 }
      );
      applyAreaAnalysisPayload(data, options);
    } catch (error) {
      console.error('Area analysis failed:', error);
      const errorMessage = error instanceof Error ? error.message : 'Area analysis failed. Check server logs for details.';
      options.onPushMessage('assistant', errorMessage);
    } finally {
      areaDrawActiveRef.current = false;
      setAreaAnalysisLoading(false);
      setAreaDrawActive(false);
      window.dispatchEvent(new CustomEvent('area-draw-interaction', { detail: { active: false } }));
      if (drawRef.current) {
        drawRef.current.changeMode('simple_select');
      }
    }
  }, [areaPolygon, areaAnalysisTimeoutMs, applyAreaAnalysisPayload, activeCvRunId]);

  return {
    areaPolygon,
    areaAggregateGeojson,
    areaDrawActive,
    areaAnalysisLoading,
    areaSummary,
    areaBBox,
    drawRef,
    startAreaDraw,
    clearAreaDraw,
    confirmArea,
    clearAreaOverlay,
  };
};

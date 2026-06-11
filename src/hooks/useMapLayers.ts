import { useCallback, useRef } from 'react';
// import type mapboxgl from 'mapbox-gl';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';
import type { WorkzoneLine, LayerBehaviorMode, VehicleFeatureProperties } from '../features/map/types';

export interface UseMapLayersParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  mapInitialized: boolean;
  setMapInitialized: React.Dispatch<React.SetStateAction<boolean>>;
  workzoneLines: WorkzoneLine[];
  layerBehaviorMode: LayerBehaviorMode;
  cvRoadGeojson: CvRoadAggregateGeoJSON | null;
  hasAreaAggregateOverlay: boolean;
  useRoadTiles: boolean;
  roadTileUrlResolved: string | null;
  mapDataset: any[];
  vehicleData: any[];
  popupRef: React.MutableRefObject<mapboxgl.Popup | null>;
  clickPopupRef: React.MutableRefObject<mapboxgl.Popup | null>;
  showCvRoadPinnedPanel: (props: any) => void;
  onCrashAnalyze?: (detail: any) => void;
  onWorkzoneAnalyze?: (detail: any) => void;
}

export interface UseMapLayersResult {
  syncLayerVisibility: () => void;
  rebuildVehicleLayers: (map: mapboxgl.Map, geojson: GeoJSON.FeatureCollection<GeoJSON.Point, VehicleFeatureProperties>) => void;
  applyRoadSegmentFocusFilter: (segmentIds: string[] | null, roadNames: string[] | null) => void;
}

export const useMapLayers = (params: UseMapLayersParams): UseMapLayersResult => {
  const {
    mapRef,
    mapReady,
    workzoneLines,
    layerBehaviorMode,
    cvRoadGeojson,
    hasAreaAggregateOverlay,
    useRoadTiles,
  } = params;
  
  const roadFocusFilterKeyRef = useRef<string>('__all__');

  const syncLayerVisibility = useCallback(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    try {
      const hasWorkzones = workzoneLines.length > 0;
      const hasExclusiveWorkzones = hasWorkzones && workzoneLines.every((line) => line.exclusive !== false);
      const hasRoadAgg = (cvRoadGeojson?.features.length ?? 0) > 0;
      const showRoadNetwork =
        layerBehaviorMode !== 'focus-selection' && !hasExclusiveWorkzones && !hasAreaAggregateOverlay;
      const showRoadTiles = useRoadTiles && showRoadNetwork;
      const showRoadGeo = !useRoadTiles && hasRoadAgg && showRoadNetwork;

      if (map.getLayer('cv-road-lines')) {
        map.setLayoutProperty('cv-road-lines', 'visibility', showRoadGeo ? 'visible' : 'none');
      }
      if (map.getLayer('cv-road-tiles')) {
        map.setLayoutProperty('cv-road-tiles', 'visibility', showRoadTiles ? 'visible' : 'none');
      }
      if (map.getLayer('cv-road-tiles-hit')) {
        map.setLayoutProperty('cv-road-tiles-hit', 'visibility', showRoadTiles ? 'visible' : 'none');
      }
      // Hide Mapbox circle layers - deck.gl renders points instead
      if (map.getLayer('vehicle-points')) {
        map.setLayoutProperty('vehicle-points', 'visibility', 'none');
      }
      if (map.getLayer('vehicle-points-hit')) {
        map.setLayoutProperty('vehicle-points-hit', 'visibility', 'none');
      }
      if (map.getLayer('crash-points')) {
        map.setLayoutProperty('crash-points', 'visibility', 'none');
      }
      if (map.getLayer('crash-points-hit')) {
        map.setLayoutProperty('crash-points-hit', 'visibility', 'none');
      }
      if (map.getLayer('workzone-lines')) {
        map.setLayoutProperty('workzone-lines', 'visibility', hasWorkzones ? 'visible' : 'none');
      }
    } catch (err) {
      console.debug('[Map] syncLayerVisibility skipped (map destroyed):', err);
    }
  }, [mapReady, layerBehaviorMode, workzoneLines, cvRoadGeojson, hasAreaAggregateOverlay, useRoadTiles, mapRef]);

  const rebuildVehicleLayers = useCallback((map: mapboxgl.Map, geojson: GeoJSON.FeatureCollection<GeoJSON.Point, VehicleFeatureProperties>) => {
    // Guard against map being destroyed
    if (!map || !mapRef.current) return;
    try {
      if (map.getLayer('vehicle-points')) {
        map.removeLayer('vehicle-points');
      }
      if (map.getLayer('vehicle-points-hit')) {
        map.removeLayer('vehicle-points-hit');
      }
      if (map.getLayer('crash-points')) {
        map.removeLayer('crash-points');
      }
      if (map.getLayer('crash-points-hit')) {
        map.removeLayer('crash-points-hit');
      }
      if (map.getSource('vehicle-data')) {
        map.removeSource('vehicle-data');
      }

      map.addSource('vehicle-data', {
        type: 'geojson',
        data: geojson,
      });

      map.addLayer({
        id: 'vehicle-points',
        type: 'circle',
        source: 'vehicle-data',
        filter: ['!', ['in', ['get', 'type'], ['literal', ['Crash', 'crash']]]],
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, ['case', ['==', ['get', 'type'], 'HardBrake'], 2, 1.5],
            10, ['case', ['==', ['get', 'type'], 'HardBrake'], 3, 2],
            12, ['case', ['==', ['get', 'type'], 'HardBrake'], 4.5, 3],
            14, ['case', ['==', ['get', 'type'], 'HardBrake'], 6, 4],
            16, ['case', ['==', ['get', 'type'], 'HardBrake'], 7, 5]
          ],
          'circle-color': [
            'case',
            ['==', ['get', 'type'], 'HardBrake'],
            [
              'interpolate',
              ['linear'],
              ['*', -1, ['coalesce', ['get', 'accX'], 0]],
              0, '#efe6ff',
              0.2, '#c7a4ff',
              0.4, '#9b6bff',
              0.6, '#6a3dcb',
              0.9, '#3a1a7a'
            ],
            [
              'let',
              'speedDelta',
              ['-',
                ['coalesce', ['get', 'speed'], 0],
                ['coalesce', ['get', 'speedLimit'], ['get', 'SpeedLimitMPH'], 0]
              ],
                  [
                'case',
                ['<', ['var', 'speedDelta'], -10], '#e53935',
                ['>', ['var', 'speedDelta'], 10], '#8b0000',
                '#2e7d32'
              ]
            ]
          ],
          'circle-opacity': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 0.4,
            10, 0.55,
            12, 0.7,
            14, 0.85,
            16, 0.95
          ],
          'circle-stroke-width': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 0,
            10, 0.3,
            12, [
              'case',
              ['==', ['get', 'type'], 'HardBrake'], 1.2,
              0.6
            ],
            14, [
              'case',
              ['==', ['get', 'type'], 'HardBrake'], 1.5,
              0.8
            ],
            16, [
              'case',
              ['==', ['get', 'type'], 'HardBrake'], 2,
              1
            ]
          ],
          'circle-stroke-color': [
            'case',
            ['==', ['get', 'type'], 'HardBrake'],
            '#ffffff',
            'rgba(255, 255, 255, 0.8)'
          ],
          'circle-stroke-opacity': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 0,
            10, 0.3,
            12, 0.6,
            14, 0.8,
            16, 1
          ],
          'circle-blur': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 0.5,
            12, 0.2,
            16, 0
          ]
        },
      });

      map.addLayer({
        id: 'vehicle-points-hit',
        type: 'circle',
        source: 'vehicle-data',
        filter: ['!', ['in', ['get', 'type'], ['literal', ['Crash', 'crash']]]],
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 6,
            10, 8,
            12, 10,
            14, 12,
            16, 14,
          ],
          'circle-color': 'rgba(0,0,0,0)',
          'circle-opacity': 0,
        },
      });

      map.addLayer({
        id: 'crash-points',
        type: 'circle',
        source: 'vehicle-data',
        filter: ['in', ['get', 'type'], ['literal', ['Crash', 'crash']]],
        paint: {
          'circle-radius': 8,
          'circle-color': [
            'match',
            ['downcase', ['coalesce', ['get', 'severity'], ['get', 'crashSeverity'], '']],
            'fatal', '#8b0000',
            'suspected serious injury', '#ff0000',
            'suspected minor injury', '#ff6b00',
            'property damage only', '#ffd700',
            '#ff0000'
          ],
          'circle-opacity': 0.85,
          'circle-stroke-width': 2,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-opacity': 0.9,
        },
      });

      map.addLayer({
        id: 'crash-points-hit',
        type: 'circle',
        source: 'vehicle-data',
        filter: ['in', ['get', 'type'], ['literal', ['Crash', 'crash']]],
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            8, 10,
            10, 12,
            12, 14,
            14, 16,
            16, 18,
          ],
          'circle-color': 'rgba(0,0,0,0)',
          'circle-opacity': 0,
        },
      });
    } catch (err) {
      console.debug('[Map] rebuildVehicleLayers skipped (map destroyed):', err);
    }
  }, [mapRef]);

  const applyRoadSegmentFocusFilter = useCallback((segmentIds: string[] | null, roadNames: string[] | null = null) => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const normalized = (segmentIds || [])
      .map((id) => String(id).trim())
      .filter(Boolean);
    const normalizedNamesSet = new Set<string>();
    (roadNames || []).forEach((name) => {
      const raw = String(name ?? '').trim().toLowerCase();
      if (!raw) return;
      const base = raw.replace(/\s+/g, ' ').trim();
      if (base) normalizedNamesSet.add(base);
      const dashToSpace = base.replace(/-/g, ' ').replace(/\s+/g, ' ').trim();
      if (dashToSpace) normalizedNamesSet.add(dashToSpace);
      const spaceToDash = base.replace(/\s+/g, '-').trim();
      if (spaceToDash) normalizedNamesSet.add(spaceToDash);
    });
    const normalizedNames = Array.from(normalizedNamesSet);
    const sortedIds = Array.from(new Set(normalized)).sort();
    const sortedNames = Array.from(new Set(normalizedNames)).sort();
    const key = `${sortedIds.join('|')}::${sortedNames.join('|')}` || '__all__';
    if (roadFocusFilterKeyRef.current === key) return;
    roadFocusFilterKeyRef.current = key;

    const layerIds = ['cv-road-tiles', 'cv-road-tiles-hit', 'cv-road-lines'];
    const idExpr = ([
          'in',
          ['to-string', ['coalesce', ['get', 'road_segment_id'], ['get', 'roadSegmentId'], ['get', 'RoadSegmentId'], ['get', 'way_id'], ['get', 'wayId'], '']],
          ['literal', sortedIds.slice(0, 1500)],
        ] as any);
    const roadLabelExpr = [
      'downcase',
      [
        'to-string',
        ['coalesce', ['get', 'road_name'], ['get', 'label'], ['get', 'name'], ['get', 'ref'], ['get', 'roadName'], ''],
      ],
    ] as any;
    const nameTerms = sortedNames.slice(0, 200);
    const nameExpr = (
      nameTerms.length
        ? ['any', ...nameTerms.map((term) => ['>=', ['index-of', term, roadLabelExpr], 0])]
        : null
    ) as any;

    let filterExpr: any = null;
    if (sortedIds.length && nameTerms.length) {
      filterExpr = ['any', idExpr, nameExpr];
    } else if (sortedIds.length) {
      filterExpr = idExpr;
    } else if (nameTerms.length) {
      filterExpr = nameExpr;
    }

    layerIds.forEach((layerId) => {
      if (!map.getLayer(layerId)) return;
      try {
        map.setFilter(layerId, filterExpr as any);
      } catch (error) {
        console.debug(`[Map] Failed to apply filter for ${layerId}`, error);
      }
    });
  }, [mapReady, mapRef]);

  return {
    syncLayerVisibility,
    rebuildVehicleLayers,
    applyRoadSegmentFocusFilter,
  };
};

import { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import { buildFeatureCollection } from '../features/map/featureBuilders';
import type { CvRoadAggregateProperties } from '../services/dataLoader';
import {
  buildCvRoadHoverHtml,
  buildWorkzonePopupHtml,
} from '../features/map/popupHtml';
import { getRoadLineColorExpression, type RoadColorMode } from '../features/map/roadColorMode';

export interface UseMapEventHandlersParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  mapInitialized: boolean;
  areaDrawActive: boolean;
  setMapInitialized: React.Dispatch<React.SetStateAction<boolean>>;
  mapDataset: any[];
  vehicleData: any[];
  useRoadTiles: boolean;
  roadTileUrlResolved: string | null;
  rebuildVehicleLayers: (map: mapboxgl.Map, geojson: GeoJSON.FeatureCollection<GeoJSON.Point, any>) => void;
  showCvRoadPinnedPanel: (props: CvRoadAggregateProperties) => void;
  clickPopupRef: React.MutableRefObject<mapboxgl.Popup | null>;
  roadColorMode: RoadColorMode;
}

export const useMapEventHandlers = (params: UseMapEventHandlersParams) => {
  const {
    mapRef,
    mapReady,
    areaDrawActive,
    setMapInitialized,
    mapDataset,
    vehicleData,
    useRoadTiles,
    roadTileUrlResolved,
    rebuildVehicleLayers,
    showCvRoadPinnedPanel,
    clickPopupRef,
    roadColorMode,
  } = params;

  const layerCleanupRef = useRef<(() => void) | undefined>(undefined);
  const deckPointHoverActiveRef = useRef(false);
  const roadColorModeRef = useRef<RoadColorMode>(roadColorMode);
  const lastHoveredRoadKeyRef = useRef<string>('');

  useEffect(() => {
    roadColorModeRef.current = roadColorMode;
  }, [roadColorMode]);

  useEffect(() => {
    if (!mapReady || !mapRef.current) {
      if (!mapReady) {
        console.log('[Map] init skipped: map not ready yet');
      } else {
        console.log('[Map] init skipped: no map instance');
      }
      return;
    }

    const map = mapRef.current;

    const addVisualization = () => {
      const initialData = mapDataset.length ? mapDataset : vehicleData;
      const geojson = buildFeatureCollection(initialData);

      rebuildVehicleLayers(map, geojson);

      if (!map.getSource('workzone-lines')) {
        map.addSource('workzone-lines', {
          type: 'geojson',
          data: {type: "FeatureCollection", features: []},
        });
      }

      if (!map.getLayer('workzone-lines')) {
        map.addLayer({
          id: 'workzone-lines',
          type: 'line',
          source: 'workzone-lines',
          layout: {
            'line-cap': 'round',
            'line-join': 'round',
          },
          paint: {
            'line-color': '#ffd54f',
            'line-width': 4,
            'line-opacity': 0.9,
          },
        });
      }

      if (!map.getSource('cv-road-lines')) {
        map.addSource('cv-road-lines', {
          type: 'geojson',
          data: { type: 'FeatureCollection', features: [] },
        });
      }

      if (!map.getLayer('cv-road-lines')) {
        map.addLayer({
          id: 'cv-road-lines',
          type: 'line',
          source: 'cv-road-lines',
          layout: {
            'line-cap': 'round',
            'line-join': 'round',
          },
          paint: {
            'line-color': getRoadLineColorExpression(roadColorModeRef.current),
            'line-width': 3,
            'line-opacity': 0.85,
          },
        });
      }

      if (useRoadTiles && roadTileUrlResolved) {
        const styleSource = (map.getStyle()?.sources?.['cv-road-tiles'] as { tiles?: string[] } | undefined);
        const existingTileUrl = Array.isArray(styleSource?.tiles) ? styleSource?.tiles[0] : null;
        if (map.getSource('cv-road-tiles') && existingTileUrl && existingTileUrl !== roadTileUrlResolved) {
          if (map.getLayer('cv-road-tiles-hit')) map.removeLayer('cv-road-tiles-hit');
          if (map.getLayer('cv-road-tiles')) map.removeLayer('cv-road-tiles');
          map.removeSource('cv-road-tiles');
        }

        if (!map.getSource('cv-road-tiles')) {
          map.addSource('cv-road-tiles', {
            type: 'vector',
            tiles: [roadTileUrlResolved],
            minzoom: 0,
            maxzoom: 14,
          });
        }

        if (!map.getLayer('cv-road-tiles')) {
          map.addLayer({
            id: 'cv-road-tiles',
            type: 'line',
            source: 'cv-road-tiles',
            'source-layer': 'roads',
            layout: {
              'line-cap': 'round',
              'line-join': 'round',
            },
            paint: {
              'line-color': getRoadLineColorExpression(roadColorModeRef.current),
              'line-width': [
                'interpolate',
                ['linear'],
                ['zoom'],
                6, 2.5,
                10, 4,
                14, 6
              ],
              'line-opacity': 1,
            },
          });
        }

        if (!map.getLayer('cv-road-tiles-hit')) {
          map.addLayer({
            id: 'cv-road-tiles-hit',
            type: 'line',
            source: 'cv-road-tiles',
            'source-layer': 'roads',
            layout: {
              'line-cap': 'round',
              'line-join': 'round',
            },
            paint: {
              'line-color': '#000000',
              'line-opacity': 0.01,
              'line-width': [
                'interpolate',
                ['linear'],
                ['zoom'],
                6, 16,
                10, 22,
                14, 28
              ],
            },
          });
        }
      }

      const popup = new mapboxgl.Popup({
        closeButton: false,
        closeOnClick: false,
        className: 'app-popup',
        offset: [0, -15],
        anchor: 'bottom',
      });
      if (!clickPopupRef.current) {
        clickPopupRef.current = new mapboxgl.Popup({
          closeButton: true,
          closeOnClick: true,
          className: 'app-popup-clickable',
        });
      }
      const clickPopup = clickPopupRef.current!;
      const deckHoverEventName = 'deck-point-hover-active';
      const hoverClearEventName = 'map-hover-clear';
      const handleDeckPointHoverEvent = (event: Event) => {
        const custom = event as CustomEvent<{ active?: boolean }>;
        const active = Boolean(custom.detail?.active);
        deckPointHoverActiveRef.current = active;
        if (active) {
          popup.remove();
          clickPopup.remove();
        }
      };
      window.addEventListener(deckHoverEventName, handleDeckPointHoverEvent as EventListener);
      const clearHoverUi = () => {
        try {
          map.getCanvas().style.cursor = '';
        } catch {
          // no-op
        }
        lastHoveredRoadKeyRef.current = '';
        popup.remove();
      };
      const handleExternalHoverClear = () => {
        clearHoverUi();
      };
      window.addEventListener(hoverClearEventName, handleExternalHoverClear as EventListener);

      const isAreaDrawInteractionActive = () => {
        if (areaDrawActive) return true;
        const classList = map.getContainer()?.classList;
        if (!classList) return false;
        return (
          classList.contains('mode-draw_polygon') ||
          classList.contains('mode-draw_line_string') ||
          classList.contains('mode-draw_point')
        );
      };

      const handleWorkzoneClick = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] }) => {
        if (isAreaDrawInteractionActive()) {
          popup.remove();
          clickPopup.remove();
          return;
        }
        if (deckPointHoverActiveRef.current) {
          popup.remove();
          clickPopup.remove();
          return;
        }
        if (!e.features || e.features.length === 0) return;
        const feature = e.features[0];
        const props = feature.properties as any;

        const buttonId = `analyze-workzone-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
        const html = buildWorkzonePopupHtml(props, true, buttonId);

        clickPopup.setLngLat(e.lngLat).setHTML(html).addTo(map);

        const popupEl = clickPopup.getElement();
        const analyzeButton = popupEl?.querySelector(`#${buttonId}`) as HTMLButtonElement | null;
        if (analyzeButton) {
          analyzeButton.addEventListener('click', (evt) => {
            evt.preventDefault();
            evt.stopPropagation();
            const startDate = props.startDate && String(props.startDate).trim() ? props.startDate : null;
            const endDate = props.endDate && String(props.endDate).trim() ? props.endDate : null;
            window.dispatchEvent(new CustomEvent('workzone-analyze', {
              detail: {
                workzone_id: props.id ?? null,
                road_segment_id: props.roadSegmentId ?? props.road_segment_id ?? null,
                start_date: startDate,
                end_date: endDate,
                dataset_id: props.datasetId ?? null,
                distance_m: 200,
              },
            }));
            clickPopup.remove();
          });
        }
      };

      const handleLineMouseEnter = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] }) => {
        if (isAreaDrawInteractionActive()) {
          map.getCanvas().style.cursor = '';
          popup.remove();
          return;
        }
        if (deckPointHoverActiveRef.current) {
          map.getCanvas().style.cursor = '';
          popup.remove();
          return;
        }
        map.getCanvas().style.cursor = 'pointer';
        if (!e.features || e.features.length === 0) return;
        const feature = e.features[0];
        const props = feature.properties as any;
        const html = buildWorkzonePopupHtml(props, false);
        popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
      };

      const handleLineMouseLeave = () => {
        map.getCanvas().style.cursor = '';
        popup.remove();
      };

      const handleMapMouseOut = () => {
        clearHoverUi();
      };

      const getRoadFeatureKey = (feature: mapboxgl.MapboxGeoJSONFeature | undefined): string => {
        const p = (feature?.properties ?? {}) as any;
        const segmentId = p.road_segment_id ?? p.segment_id ?? p.way_id;
        if (segmentId != null && String(segmentId).trim()) {
          return String(segmentId);
        }
        const name = p.road_name ?? p.label ?? p.ref ?? p.name ?? p.highway ?? '';
        const direction = p.direction ?? '';
        const startTs = p.start_ts ?? '';
        const endTs = p.end_ts ?? '';
        const points = p.point_count ?? '';
        const vehicleTotal = p.unique_vehicles_total ?? '';
        const avgSpeed = p.avg_speed_mph ?? p.avg_speed ?? '';
        return `${name}|${direction}|${startTs}|${endTs}|${points}|${vehicleTotal}|${avgSpeed}`;
      };

      const pickRoadFeature = (features: mapboxgl.MapboxGeoJSONFeature[]): mapboxgl.MapboxGeoJSONFeature => {
        if (features.length <= 1) return features[0];
        const previousKey = lastHoveredRoadKeyRef.current;
        if (!previousKey) return features[0];
        const alternate = features.find((f) => {
          const key = getRoadFeatureKey(f);
          return key && key !== previousKey;
        });
        return alternate ?? features[0];
      };

      const handleCvRoadMouseEnter = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] }) => {
        if (isAreaDrawInteractionActive()) {
          map.getCanvas().style.cursor = '';
          popup.remove();
          return;
        }
        if (deckPointHoverActiveRef.current) {
          map.getCanvas().style.cursor = '';
          popup.remove();
          return;
        }
        map.getCanvas().style.cursor = 'pointer';
        if (!e.features || e.features.length === 0) return;
        const feature = pickRoadFeature(e.features);
        const props = feature.properties as CvRoadAggregateProperties;
        const html = buildCvRoadHoverHtml(props, roadColorModeRef.current);
        popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
        lastHoveredRoadKeyRef.current = getRoadFeatureKey(feature);
      };

      const handleCvRoadMouseLeave = () => {
        map.getCanvas().style.cursor = '';
        lastHoveredRoadKeyRef.current = '';
        popup.remove();
      };

      const handleCvRoadMouseMove = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] }) => {
        if (isAreaDrawInteractionActive()) {
          clearHoverUi();
          return;
        }
        if (deckPointHoverActiveRef.current) {
          clearHoverUi();
          return;
        }
        if (!e.features || e.features.length === 0) {
          clearHoverUi();
          return;
        }
        const picked = pickRoadFeature(e.features);
        const props = picked.properties as CvRoadAggregateProperties;
        const featureKey = getRoadFeatureKey(picked);
        map.getCanvas().style.cursor = 'pointer';
        if (featureKey && featureKey === lastHoveredRoadKeyRef.current && popup.isOpen()) {
          popup.setLngLat(e.lngLat);
          return;
        }
        const html = buildCvRoadHoverHtml(props, roadColorModeRef.current);
        popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
        lastHoveredRoadKeyRef.current = featureKey;
      };

      const handleCvRoadFallbackClick = (e: mapboxgl.MapMouseEvent) => {
        if (isAreaDrawInteractionActive()) {
          popup.remove();
          clickPopup.remove();
          return;
        }
        if (deckPointHoverActiveRef.current) {
          popup.remove();
          clickPopup.remove();
          return;
        }
        const blockingLayers = ['vehicle-points-hit', 'vehicle-points', 'crash-points-hit', 'crash-points', 'workzone-lines'].filter((layerId) => map.getLayer(layerId));
        if (blockingLayers.length > 0) {
          const blockingHit = map.queryRenderedFeatures(e.point, { layers: blockingLayers as any });
          if (blockingHit && blockingHit.length > 0) return;
        }
        const candidateLayers = ['area-aggregate-lines', 'cv-road-tiles-hit', 'cv-road-tiles', 'cv-road-lines'].filter((layerId) => {
          if (!map.getLayer(layerId)) return false;
          return map.getLayoutProperty(layerId, 'visibility') !== 'none';
        });
        if (candidateLayers.length === 0) {
          console.debug('[RoadClickFallback] no visible road candidate layers');
          return;
        }
        const features = map.queryRenderedFeatures(e.point, { layers: candidateLayers as any });
        if (!features || features.length === 0) {
          console.debug('[RoadClickFallback] click had no rendered road features', {
            candidateLayers,
            point: e.point,
          });
          return;
        }
        const picked = features.find((f) => {
          const p = (f?.properties ?? {}) as any;
          return p.road_segment_id || p.way_id || p.road_name || p.label || p.ref || p.name || p.highway || p.avg_speed_mph != null || p.point_count != null;
        }) ?? features[0];
        const props = picked.properties as CvRoadAggregateProperties;
        if (!props) return;
        console.debug('[RoadClickFallback] picked feature', {
          layerId: picked.layer?.id,
          road_segment_id: (props as any)?.road_segment_id,
          road_name: (props as any)?.road_name,
          keys: Object.keys((props as any) ?? {}).slice(0, 12),
        });
        clickPopup.remove();
        showCvRoadPinnedPanel(props);
      };

      map.on('mouseenter', 'workzone-lines', handleLineMouseEnter);
      map.on('mouseleave', 'workzone-lines', handleLineMouseLeave);
      map.on('click', 'workzone-lines', handleWorkzoneClick);
      if (map.getLayer('cv-road-lines')) {
        map.on('mouseenter', 'cv-road-lines', handleCvRoadMouseEnter);
        map.on('mousemove', 'cv-road-lines', handleCvRoadMouseMove);
        map.on('mouseleave', 'cv-road-lines', handleCvRoadMouseLeave);
      }
      if (map.getLayer('area-aggregate-lines')) {
        map.on('mouseenter', 'area-aggregate-lines', handleCvRoadMouseEnter);
        map.on('mousemove', 'area-aggregate-lines', handleCvRoadMouseMove);
        map.on('mouseleave', 'area-aggregate-lines', handleCvRoadMouseLeave);
      }
      if (map.getLayer('cv-road-tiles-hit')) {
        map.on('mouseenter', 'cv-road-tiles-hit', handleCvRoadMouseEnter);
        map.on('mousemove', 'cv-road-tiles-hit', handleCvRoadMouseMove);
        map.on('mouseleave', 'cv-road-tiles-hit', handleCvRoadMouseLeave);
      }
      map.on('click', handleCvRoadFallbackClick);
      map.on('mouseout', handleMapMouseOut);

      const bounds = new mapboxgl.LngLatBounds();
      let validPoints = 0;

      geojson.features.forEach((feature: GeoJSON.Feature<GeoJSON.Point>) => {
        const coords = feature.geometry.coordinates as [number, number];
        if (Array.isArray(coords) && coords.length === 2) {
          bounds.extend(coords);
          validPoints++;
        }
      });

      if (validPoints > 0) {
        map.fitBounds(bounds, { padding: 50, maxZoom: 12 });
      }

      return () => {
        try {
          if (map && typeof map.off === 'function') {
            map.off('mouseenter', 'workzone-lines', handleLineMouseEnter);
            map.off('mouseleave', 'workzone-lines', handleLineMouseLeave);
            map.off('click', 'workzone-lines', handleWorkzoneClick);
            if (map.getLayer('cv-road-lines')) {
              map.off('mouseenter', 'cv-road-lines', handleCvRoadMouseEnter);
              map.off('mousemove', 'cv-road-lines', handleCvRoadMouseMove);
              map.off('mouseleave', 'cv-road-lines', handleCvRoadMouseLeave);
            }
            if (map.getLayer('area-aggregate-lines')) {
              map.off('mouseenter', 'area-aggregate-lines', handleCvRoadMouseEnter);
              map.off('mousemove', 'area-aggregate-lines', handleCvRoadMouseMove);
              map.off('mouseleave', 'area-aggregate-lines', handleCvRoadMouseLeave);
            }
            try {
              if (map.getLayer('cv-road-tiles-hit')) {
                map.off('mouseenter', 'cv-road-tiles-hit', handleCvRoadMouseEnter);
                map.off('mousemove', 'cv-road-tiles-hit', handleCvRoadMouseMove);
                map.off('mouseleave', 'cv-road-tiles-hit', handleCvRoadMouseLeave);
              }
            } catch {
              // Layer might not exist, ignore
            }
            map.off('click', handleCvRoadFallbackClick);
            map.off('mouseout', handleMapMouseOut);
          }
          window.removeEventListener(deckHoverEventName, handleDeckPointHoverEvent as EventListener);
          window.removeEventListener(hoverClearEventName, handleExternalHoverClear as EventListener);
          deckPointHoverActiveRef.current = false;
          lastHoveredRoadKeyRef.current = '';
          popup.remove();
        } catch (err) {
          console.debug('[Map] Visualization cleanup skipped (map destroyed):', err);
        }
      };
    };

    const initializeLayers = () => {
      try {
        layerCleanupRef.current = addVisualization();
        setMapInitialized(true);
        console.log('✅ Map initialization complete');
        return true;
      } catch (err) {
        console.error('Failed to initialize layers:', err);
        setMapInitialized(false);
        return false;
      }
    };

    const styleLoadHandler = () => {
      const ok = initializeLayers();
      if (ok) {
        map.off('style.load', styleLoadHandler);
        map.off('load', styleLoadHandler);
      }
    };

    if (!initializeLayers()) {
      map.on('style.load', styleLoadHandler);
      map.on('load', styleLoadHandler);
    }

    return () => {
      try {
        if (map && typeof map.off === 'function') {
          map.off('style.load', styleLoadHandler);
          map.off('load', styleLoadHandler);
        }
        layerCleanupRef.current?.();
      } catch (err) {
        console.debug('[Map] Style cleanup skipped (map destroyed):', err);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, areaDrawActive, useRoadTiles, roadTileUrlResolved, mapDataset, vehicleData, rebuildVehicleLayers, showCvRoadPinnedPanel, clickPopupRef, mapRef]);

  // Update road-layer color when color mode changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const expr = getRoadLineColorExpression(roadColorMode);
    if (map.getLayer('cv-road-tiles')) {
      map.setPaintProperty('cv-road-tiles', 'line-color', expr);
    }
    if (map.getLayer('cv-road-lines')) {
      map.setPaintProperty('cv-road-lines', 'line-color', expr);
    }
    if (map.getLayer('area-aggregate-lines')) {
      map.setPaintProperty('area-aggregate-lines', 'line-color', expr);
    }
  }, [roadColorMode, mapReady, mapRef]);
};

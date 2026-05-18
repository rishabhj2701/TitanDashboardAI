import { useEffect } from 'react';
import type { Map as MapboxMap, GeoJSONSource } from 'mapbox-gl';
import type { AreaAggregateGeoJSON } from './useAreaAnalysis';
import type { LayerBehaviorMode } from '../features/map/types';
import { getRoadLineColorExpression, type RoadColorMode } from '../features/map/roadColorMode';

export interface UseAreaAggregateRoadLinesParams {
  mapRef: React.RefObject<MapboxMap | null>;
  mapReady: boolean;
  layerBehaviorMode: LayerBehaviorMode;
  areaAggregateGeojson: AreaAggregateGeoJSON | null;
  syncLayerVisibility: () => void;
  roadColorMode: RoadColorMode;
}

const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

export const useAreaAggregateRoadLines = (params: UseAreaAggregateRoadLinesParams) => {
  const { mapRef, mapReady, layerBehaviorMode, areaAggregateGeojson, syncLayerVisibility, roadColorMode } = params;

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const data = areaAggregateGeojson ?? (EMPTY_FC as AreaAggregateGeoJSON);
    const hasLines = (areaAggregateGeojson?.features.length ?? 0) > 0;

    const apply = () => {
      if (!map.getSource('area-aggregate-lines')) {
        map.addSource('area-aggregate-lines', {
          type: 'geojson',
          data,
        });
      } else {
        const source = map.getSource('area-aggregate-lines') as GeoJSONSource;
        source.setData(data);
      }

      if (!map.getLayer('area-aggregate-lines')) {
        map.addLayer({
          id: 'area-aggregate-lines',
          type: 'line',
          source: 'area-aggregate-lines',
          layout: {
            'line-cap': 'round',
            'line-join': 'round',
          },
          paint: {
            'line-color': getRoadLineColorExpression('vehicle-count'),
            'line-width': [
              'interpolate',
              ['linear'],
              ['zoom'],
              6, 2.5,
              10, 4,
              14, 6
            ],
            'line-opacity': 0.95,
          },
        });
      }

      const show = hasLines && layerBehaviorMode !== 'focus-selection';
      if (map.getLayer('area-aggregate-lines')) {
        map.setLayoutProperty('area-aggregate-lines', 'visibility', show ? 'visible' : 'none');
      }
      syncLayerVisibility();
    };

    if (map.isStyleLoaded()) {
      apply();
    } else {
      map.once('styledata', apply);
    }
  }, [mapRef, mapReady, layerBehaviorMode, areaAggregateGeojson, syncLayerVisibility]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (!map.getLayer('area-aggregate-lines')) return;
    map.setPaintProperty('area-aggregate-lines', 'line-color', getRoadLineColorExpression(roadColorMode));
  }, [mapReady, mapRef, roadColorMode]);
};

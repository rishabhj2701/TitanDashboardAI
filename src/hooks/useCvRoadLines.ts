import { useEffect } from 'react';
import mapboxgl from 'mapbox-gl';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';
import { getRoadLineColorExpression, type RoadColorMode } from '../features/map/roadColorMode';

export interface UseCvRoadLinesParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  cvRoadGeojson: CvRoadAggregateGeoJSON | null;
  useRoadTiles: boolean;
  syncLayerVisibility: () => void;
  roadColorMode: RoadColorMode;
}

export const useCvRoadLines = (params: UseCvRoadLinesParams) => {
  const { mapRef, mapReady, cvRoadGeojson, useRoadTiles, syncLayerVisibility, roadColorMode } = params;

  useEffect(() => {
    if (!mapReady || !mapRef.current || !cvRoadGeojson || useRoadTiles) {
      return;
    }

    const map = mapRef.current;

    const updateCvRoadLines = () => {
      const hasLines = cvRoadGeojson.features.length > 0;

      if (!map.getSource('cv-road-lines')) {
        map.addSource('cv-road-lines', {
          type: 'geojson',
          data: cvRoadGeojson,
        });
      } else {
        const source = map.getSource('cv-road-lines') as mapboxgl.GeoJSONSource;
        source.setData(cvRoadGeojson);
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
            'line-color': getRoadLineColorExpression('vehicle-count'),
            'line-width': 3,
            'line-opacity': 0.85,
          },
        });
      }

      syncLayerVisibility();

      if (hasLines) {
        try {
          const bounds = new mapboxgl.LngLatBounds();
          let valid = 0;
          cvRoadGeojson.features.forEach((feature) => {
            const geom = feature.geometry;
            if (!geom) return;
            if (geom.type === 'LineString') {
              geom.coordinates.forEach((coord) => {
                const [lon, lat] = coord as [number, number];
                if (Number.isFinite(lon) && Number.isFinite(lat)) {
                  bounds.extend([lon, lat]);
                  valid += 1;
                }
              });
            } else if (geom.type === 'MultiLineString') {
              geom.coordinates.forEach((line) => {
                line.forEach((coord) => {
                  const [lon, lat] = coord as [number, number];
                  if (Number.isFinite(lon) && Number.isFinite(lat)) {
                    bounds.extend([lon, lat]);
                    valid += 1;
                  }
                });
              });
            }
          });
          if (valid > 0) {
            map.fitBounds(bounds, { padding: 60, maxZoom: 11 });
          }
        } catch (error) {
          console.error('[App] Error fitting CV road bounds:', error);
        }
      }
    };

    if (map.isStyleLoaded()) {
      updateCvRoadLines();
    } else {
      map.once('styledata', () => {
        updateCvRoadLines();
      });
    }
  }, [cvRoadGeojson, mapReady, syncLayerVisibility, useRoadTiles, mapRef]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || useRoadTiles) return;
    if (!map.getLayer('cv-road-lines')) return;
    map.setPaintProperty('cv-road-lines', 'line-color', getRoadLineColorExpression(roadColorMode));
  }, [mapReady, mapRef, roadColorMode, useRoadTiles]);
};

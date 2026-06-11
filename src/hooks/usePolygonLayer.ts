import { useEffect } from 'react';
// import type mapboxgl from 'mapbox-gl';

export interface UsePolygonLayerParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  areaPolygon: GeoJSON.Polygon | null;
}

export const usePolygonLayer = (params: UsePolygonLayerParams) => {
  const { mapRef, mapReady, areaPolygon } = params;

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;

    const updatePolygonLayer = () => {
      // Guard against map being destroyed
      if (!map || !mapRef.current) return;
      try {
        const geojson: GeoJSON.FeatureCollection<GeoJSON.Polygon> = {
          type: 'FeatureCollection',
          features: areaPolygon
            ? [{ type: 'Feature', geometry: areaPolygon, properties: {} }]
            : [],
        };

        const styleLayers = map.getStyle()?.layers ?? [];
        const overlayLayerIds = new Set([
          'cv-road-tiles',
          'cv-road-tiles-hit',
          'cv-road-lines',
          'area-aggregate-lines',
          'workzone-lines',
          'vehicle-points',
          'vehicle-points-hit',
          'crash-points',
          'crash-points-hit',
        ]);
        const beforeId = styleLayers.find((layer) => overlayLayerIds.has(layer.id))?.id;

        if (!map.getSource('area-polygon')) {
          map.addSource('area-polygon', {
            type: 'geojson',
            data: geojson,
          });
        } else {
          const source = map.getSource('area-polygon') as mapboxgl.GeoJSONSource;
          source.setData(geojson);
        }

        if (!map.getLayer('area-polygon-fill')) {
          map.addLayer({
            id: 'area-polygon-fill',
            type: 'fill',
            source: 'area-polygon',
            paint: {
              'fill-color': '#00bcd4',
              'fill-opacity': 0.28,
            },
          }, beforeId);
        } else if (beforeId) {
          map.moveLayer('area-polygon-fill', beforeId);
        }

        if (!map.getLayer('area-polygon-outline')) {
          map.addLayer({
            id: 'area-polygon-outline',
            type: 'line',
            source: 'area-polygon',
            paint: {
              'line-color': '#00bcd4',
              'line-width': 2,
            },
          }, beforeId);
        } else if (beforeId) {
          map.moveLayer('area-polygon-outline', beforeId);
        }
      } catch (err) {
        console.debug('[Map] updatePolygonLayer skipped (map destroyed):', err);
      }
    };

    if (map.isStyleLoaded()) {
      updatePolygonLayer();
    } else {
      map.once('styledata', updatePolygonLayer);
    }
  }, [mapReady, areaPolygon, mapRef]);
};

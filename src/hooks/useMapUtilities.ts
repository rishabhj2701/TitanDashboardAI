import { useCallback } from 'react';
import mapboxgl from 'mapbox-gl';
import { getRoadsBbox } from '../api/tileClient';
import type { CvRoadAggregateGeoJSON } from '../services/dataLoader';

export interface UseMapUtilitiesParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  useRoadTiles: boolean;
  activeCvRunId: string;
  cvRoadGeojson: CvRoadAggregateGeoJSON | null;
}

export interface UseMapUtilitiesResult {
  resetMapToRoadNetwork: () => Promise<void>;
  captureScreenshot: () => void;
}

export const useMapUtilities = ({
  mapRef,
  useRoadTiles,
  activeCvRunId,
  cvRoadGeojson,
}: UseMapUtilitiesParams): UseMapUtilitiesResult => {
  const resetMapToRoadNetwork = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;

    const fitToBounds = (minLon: number, minLat: number, maxLon: number, maxLat: number) => {
      if (![minLon, minLat, maxLon, maxLat].every((v) => Number.isFinite(v))) return false;
      const bounds = new mapboxgl.LngLatBounds([minLon, minLat], [maxLon, maxLat]);
      if (bounds.isEmpty()) return false;
      map.fitBounds(bounds, { padding: 60, maxZoom: 11 });
      return true;
    };

    if (!useRoadTiles && cvRoadGeojson?.features?.length) {
      try {
        const bounds = new mapboxgl.LngLatBounds();
        let valid = 0;
        cvRoadGeojson.features.forEach((feature) => {
          const geom = feature.geometry;
          if (!geom) return;
          if (geom.type === 'LineString') {
            geom.coordinates.forEach(([lon, lat]) => {
              if (Number.isFinite(lon) && Number.isFinite(lat)) {
                bounds.extend([lon, lat]);
                valid += 1;
              }
            });
          } else if (geom.type === 'MultiLineString') {
            geom.coordinates.forEach((line) => {
              line.forEach(([lon, lat]) => {
                if (Number.isFinite(lon) && Number.isFinite(lat)) {
                  bounds.extend([lon, lat]);
                  valid += 1;
                }
              });
            });
          }
        });
        if (valid > 0 && !bounds.isEmpty()) {
          map.fitBounds(bounds, { padding: 60, maxZoom: 11 });
          return;
        }
      } catch (error) {
        console.debug('[Map] Failed to fit to cached road geojson bounds', error);
      }
    }

    try {
      const roadTileDatasetId = (activeCvRunId || '').trim();
      const data = await getRoadsBbox(useRoadTiles ? (roadTileDatasetId || undefined) : undefined);
      const bbox = data?.bbox;
      if (!bbox) return;
      fitToBounds(bbox.minLon, bbox.minLat, bbox.maxLon, bbox.maxLat);
    } catch (error) {
      console.debug('[Map] Failed to reset to road bounds', error);
    }
  }, [mapRef, useRoadTiles, activeCvRunId, cvRoadGeojson]);

  const captureScreenshot = useCallback(() => {
    if (!mapRef.current) return;
    const map = mapRef.current;
    const canvas = map.getCanvas();
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = canvas.width;
    tempCanvas.height = canvas.height;
    const ctx = tempCanvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(canvas, 0, 0);
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const filename = `titan-map-${timestamp}.png`;
    tempCanvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 'image/png');
  }, [mapRef]);

  return {
    resetMapToRoadNetwork,
    captureScreenshot,
  };
};

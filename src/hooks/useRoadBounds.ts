import { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import { getRoadsBbox } from '../api/tileClient';

export interface UseRoadBoundsParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  useRoadTiles: boolean;
  activeCvRunId: string | null;
}

export const useRoadBounds = (params: UseRoadBoundsParams) => {
  const { mapRef, mapReady, useRoadTiles, activeCvRunId } = params;
  const roadBoundsAppliedRef = useRef(false);

  useEffect(() => {
    if (!mapReady || !mapRef.current || !useRoadTiles) return;
    if (roadBoundsAppliedRef.current) return;
    const map = mapRef.current;

    const fetchBounds = async () => {
      try {
        const data = await getRoadsBbox((activeCvRunId || '').trim() || undefined);
        const bbox = data?.bbox;
        if (!bbox) return;
        const { minLon, minLat, maxLon, maxLat } = bbox;
        if (![minLon, minLat, maxLon, maxLat].every((v) => Number.isFinite(v))) return;
        const bounds = new mapboxgl.LngLatBounds([minLon, minLat], [maxLon, maxLat]);
        if (!bounds.isEmpty()) {
          roadBoundsAppliedRef.current = true;
          map.fitBounds(bounds, { padding: 60, maxZoom: 11 });
        }
      } catch (err) {
        console.debug('[Map] road bbox fetch failed', err);
      }
    };

    fetchBounds();
  }, [mapReady, useRoadTiles, activeCvRunId, mapRef]);
};

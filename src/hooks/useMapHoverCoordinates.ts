import { useEffect } from 'react';
// import type mapboxgl from 'mapbox-gl';

export interface UseMapHoverCoordinatesParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  setHoverCoord: React.Dispatch<React.SetStateAction<[number, number] | null>>;
}

export const useMapHoverCoordinates = (params: UseMapHoverCoordinatesParams) => {
  const { mapRef, mapReady, setHoverCoord } = params;

  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const handleMouseMove = (e: mapboxgl.MapMouseEvent) => {
      setHoverCoord([e.lngLat.lng, e.lngLat.lat]);
    };
    map.on('mousemove', handleMouseMove);
    return () => {
      try {
        if (map && typeof map.off === 'function') {
          map.off('mousemove', handleMouseMove);
        }
      } catch (err) {
        console.debug('[Map] Cleanup skipped (map destroyed):', err);
      }
    };
  }, [mapReady, setHoverCoord, mapRef]);
};

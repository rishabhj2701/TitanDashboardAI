import { useEffect } from 'react';
// import type mapboxgl from 'mapbox-gl';

export interface UseMapErrorHandlingParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
}

export const useMapErrorHandling = (params: UseMapErrorHandlingParams) => {
  const { mapRef, mapReady } = params;

  useEffect(() => {
    if (!mapRef.current || !mapReady) return;
    const map = mapRef.current;

    const handleMapError = (evt: any) => {
      const err = evt?.error || evt;
      const message = err?.message || String(err);
      const status = err?.status || err?.statusCode;
      const url = err?.url || err?.source?.url;
      console.error('[Map] Mapbox error', { message, status, url, err });
    };

    const handleStyleLoad = () => {
      console.log('✅ Map style.load event fired', {
        isStyleLoaded: map.isStyleLoaded(),
        isLoaded: map.loaded(),
      });
    };

    map.on('error', handleMapError);
    map.on('style.load', handleStyleLoad);
    return () => {
      try {
        map.off('error', handleMapError);
        map.off('style.load', handleStyleLoad);
      } catch (err) {
        console.debug('[Map] error handler cleanup skipped:', err);
      }
    };
  }, [mapReady, mapRef]);
};

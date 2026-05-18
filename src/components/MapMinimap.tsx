import { useEffect, useRef, useCallback, useState } from 'react';
import mapboxgl from 'mapbox-gl';
import { MAPBOX_TOKEN } from '../config';

interface MapMinimapProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
}

function MapMinimap({ mapRef, mapReady }: MapMinimapProps) {
  const miniRef = useRef<HTMLDivElement>(null);
  const miniMapRef = useRef<mapboxgl.Map | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!miniRef.current || collapsed) return;
    const mini = new mapboxgl.Map({
      container: miniRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [-92.5, 38.5],
      zoom: 3,
      interactive: false,
      accessToken: MAPBOX_TOKEN,
      attributionControl: false,
    });
    miniMapRef.current = mini;
    return () => { mini.remove(); miniMapRef.current = null; };
  }, [collapsed]);

  const syncMini = useCallback(() => {
    const map = mapRef.current;
    const mini = miniMapRef.current;
    if (!map || !mini) return;
    const center = map.getCenter();
    const zoom = Math.max(map.getZoom() - 4, 1);
    mini.jumpTo({ center, zoom });
  }, [mapRef]);

  useEffect(() => {
    if (!mapReady || collapsed) return;
    const map = mapRef.current;
    if (!map) return;
    const handler = () => syncMini();
    map.on('move', handler);
    syncMini();
    return () => { map.off('move', handler); };
  }, [mapReady, syncMini, collapsed, mapRef]);

  return (
    <div className={`minimap-container${collapsed ? ' collapsed' : ''}`}>
      {collapsed ? (
        <button className="minimap-expand" onClick={() => setCollapsed(false)} title="Show minimap">
          {'\u25A3'}
        </button>
      ) : (
        <>
          <button className="minimap-collapse" onClick={() => setCollapsed(true)} title="Hide minimap">
            {'\u2715'}
          </button>
          <div ref={miniRef} className="minimap-canvas" />
        </>
      )}
    </div>
  );
}

export default MapMinimap;

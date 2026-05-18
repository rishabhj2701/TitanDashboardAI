import { useState, useCallback, useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';

interface MeasurementToolProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
}

function haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3958.8; // miles
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function MeasurementTool({ mapRef, mapReady }: MeasurementToolProps) {
  const [active, setActive] = useState(false);
  const [pointA, setPointA] = useState<[number, number] | null>(null);
  const [pointB, setPointB] = useState<[number, number] | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);

  const clearMeasure = useCallback(() => {
    setPointA(null);
    setPointB(null);
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];
    const map = mapRef.current;
    if (map) {
      if (map.getLayer('measure-line')) map.removeLayer('measure-line');
      if (map.getSource('measure-line')) map.removeSource('measure-line');
    }
  }, [mapRef]);

  const handleToggle = useCallback(() => {
    if (active) {
      clearMeasure();
      setActive(false);
    } else {
      setActive(true);
    }
  }, [active, clearMeasure]);

  useEffect(() => {
    if (!active || !mapReady) return;
    const map = mapRef.current;
    if (!map) return;

    const handler = (e: mapboxgl.MapMouseEvent) => {
      const lngLat = e.lngLat;
      const point: [number, number] = [lngLat.lng, lngLat.lat];

      setPointA(prev => {
        if (!prev) {
          // First point
          const el = document.createElement('div');
          el.className = 'measure-marker measure-marker-a';
          el.textContent = 'A';
          const marker = new mapboxgl.Marker({ element: el }).setLngLat(point).addTo(map);
          markersRef.current.push(marker);
          return point;
        }
        // Second point
        setPointB(() => {
          const el = document.createElement('div');
          el.className = 'measure-marker measure-marker-b';
          el.textContent = 'B';
          const marker = new mapboxgl.Marker({ element: el }).setLngLat(point).addTo(map);
          markersRef.current.push(marker);

          // Draw line
          const geojson: GeoJSON.FeatureCollection = {
            type: 'FeatureCollection',
            features: [{
              type: 'Feature',
              properties: {},
              geometry: { type: 'LineString', coordinates: [prev, point] }
            }]
          };
          if (map.getSource('measure-line')) {
            (map.getSource('measure-line') as mapboxgl.GeoJSONSource).setData(geojson);
          } else {
            map.addSource('measure-line', { type: 'geojson', data: geojson });
            map.addLayer({
              id: 'measure-line',
              type: 'line',
              source: 'measure-line',
              paint: {
                'line-color': '#64ffda',
                'line-width': 2,
                'line-dasharray': [4, 2],
              }
            });
          }
          return point;
        });
        return prev;
      });
    };

    map.on('click', handler);
    map.getCanvas().style.cursor = 'crosshair';
    return () => {
      map.off('click', handler);
      map.getCanvas().style.cursor = '';
    };
  }, [active, mapReady, mapRef]);

  const distance = pointA && pointB ? haversineDistance(pointA[1], pointA[0], pointB[1], pointB[0]) : null;

  return (
    <div className="measure-tool">
      <button
        className={`measure-tool-btn${active ? ' active' : ''}`}
        onClick={handleToggle}
        title="Measure Distance"
      >
        {'\u{1F4CF}'}
      </button>
      {active && distance != null && (
        <div className="measure-result">
          <span className="measure-value">{distance.toFixed(2)} mi</span>
          <span className="measure-km">({(distance * 1.60934).toFixed(2)} km)</span>
          <button className="measure-clear" onClick={() => { clearMeasure(); }}>Clear</button>
        </div>
      )}
      {active && !pointA && (
        <div className="measure-hint">Click to place point A</div>
      )}
      {active && pointA && !pointB && (
        <div className="measure-hint">Click to place point B</div>
      )}
    </div>
  );
}

export default MeasurementTool;

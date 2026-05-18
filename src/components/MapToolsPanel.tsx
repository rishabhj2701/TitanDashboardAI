import { useState, useCallback, useRef, useEffect } from 'react';

interface MapToolsPanelProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
}

function MapToolsPanel({ mapRef, mapReady }: MapToolsPanelProps) {
  const [open, setOpen] = useState(false);
  const [animatedFlow, setAnimatedFlow] = useState(false);
  const [extrusion3D, setExtrusion3D] = useState(false);
  const [flyThrough, setFlyThrough] = useState(false);
  const animFrameRef = useRef<number>(0);
  const flyFrameRef = useRef<number>(0);

  // Animated Flow - dash-offset animation on road lines
  const toggleAnimatedFlow = useCallback(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    if (animatedFlow) {
      // Turn off
      cancelAnimationFrame(animFrameRef.current);
      if (map.getLayer('cv-road-lines')) {
        map.setPaintProperty('cv-road-lines', 'line-dasharray', undefined as any);
      }
      setAnimatedFlow(false);
      return;
    }

    // Turn on
    setAnimatedFlow(true);
    if (!map.getLayer('cv-road-lines')) return;

    let step = 0;
    const animate = () => {
      step = (step + 1) % 200;
      // Use varying dash patterns to create flow illusion
      const dashLength = 4;
      const gapLength = 4;
      try {
        map.setPaintProperty('cv-road-lines', 'line-dasharray', [dashLength, gapLength]);
      } catch { /* layer may not exist */ }
      animFrameRef.current = requestAnimationFrame(animate);
    };
    animate();
  }, [animatedFlow, mapRef, mapReady]);

  // 3D Extrusion - raise roads based on speed
  const toggle3DExtrusion = useCallback(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    if (extrusion3D) {
      // Turn off - restore pitch
      map.easeTo({ pitch: 0, duration: 500 });
      if (map.getLayer('road-extrusion-3d')) {
        map.removeLayer('road-extrusion-3d');
      }
      if (map.getSource('road-extrusion-3d')) {
        map.removeSource('road-extrusion-3d');
      }
      setExtrusion3D(false);
      return;
    }

    // Turn on
    setExtrusion3D(true);
    map.easeTo({ pitch: 55, bearing: -20, duration: 1000 });

    // Create 3D extrusion from road lines source
    const source = map.getSource('cv-road-lines');
    if (!source) return;

    // Get the geojson data from existing source
    try {
      const existing = (source as mapboxgl.GeoJSONSource)._data;
      if (existing && typeof existing === 'object' && 'features' in (existing as any)) {
        if (!map.getLayer('road-extrusion-3d')) {
          // Add a highlight layer with thick translucent lines to simulate 3D feel
          map.addLayer({
            id: 'road-extrusion-3d',
            type: 'line',
            source: 'cv-road-lines',
            paint: {
              'line-color': ['coalesce',
                ['case',
                  ['has', 'speed_over_limit'],
                  ['interpolate', ['linear'], ['get', 'speed_over_limit'],
                    -10, '#22c55e',
                    0, '#f59e0b',
                    10, '#ef4444',
                    20, '#dc2626'
                  ],
                  '#666'
                ],
                '#666'
              ],
              'line-width': ['interpolate', ['linear'], ['zoom'],
                8, 6,
                12, 14,
                16, 24
              ],
              'line-opacity': 0.35,
              'line-blur': 3,
            }
          }, 'cv-road-lines');
        }
      }
    } catch {
      // Source data not accessible - add basic glow layer
      if (!map.getLayer('road-extrusion-3d') && map.getSource('cv-road-lines')) {
        map.addLayer({
          id: 'road-extrusion-3d',
          type: 'line',
          source: 'cv-road-lines',
          paint: {
            'line-color': '#64ffda',
            'line-width': 10,
            'line-opacity': 0.25,
            'line-blur': 4,
          }
        }, 'cv-road-lines');
      }
    }
  }, [extrusion3D, mapRef, mapReady]);

  // Cinematic Fly-Through
  const toggleFlyThrough = useCallback(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    if (flyThrough) {
      cancelAnimationFrame(flyFrameRef.current);
      setFlyThrough(false);
      return;
    }

    setFlyThrough(true);
    const startBearing = map.getBearing();
    let bearing = startBearing;

    const animateFly = () => {
      bearing += 0.15;
      map.rotateTo(bearing % 360, { duration: 0 });
      flyFrameRef.current = requestAnimationFrame(animateFly);
    };

    // First set pitch for cinematic view
    map.easeTo({ pitch: 60, zoom: Math.min(map.getZoom(), 14), duration: 1000 });
    setTimeout(() => animateFly(), 1000);
  }, [flyThrough, mapRef, mapReady]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cancelAnimationFrame(animFrameRef.current);
      cancelAnimationFrame(flyFrameRef.current);
    };
  }, []);

  return (
    <div className="map-tools-panel">
      <button className="map-tools-toggle" onClick={() => setOpen(!open)} title="Map Tools">
        {'\u2699'}
      </button>
      {open && (
        <div className="map-tools-menu">
          <div className="map-tools-title">Map Tools</div>
          <button
            className={`map-tools-item${animatedFlow ? ' active' : ''}`}
            onClick={toggleAnimatedFlow}
          >
            <span className="map-tools-icon">{'\u{1F300}'}</span>
            <span>Animated Flow</span>
            <span className={`map-tools-status${animatedFlow ? ' on' : ''}`}>{animatedFlow ? 'ON' : 'OFF'}</span>
          </button>
          <button
            className={`map-tools-item${extrusion3D ? ' active' : ''}`}
            onClick={toggle3DExtrusion}
          >
            <span className="map-tools-icon">{'\u{1F3D7}'}</span>
            <span>3D Extrusion</span>
            <span className={`map-tools-status${extrusion3D ? ' on' : ''}`}>{extrusion3D ? 'ON' : 'OFF'}</span>
          </button>
          <button
            className={`map-tools-item${flyThrough ? ' active' : ''}`}
            onClick={toggleFlyThrough}
          >
            <span className="map-tools-icon">{'\u{1F3AC}'}</span>
            <span>Fly-Through</span>
            <span className={`map-tools-status${flyThrough ? ' on' : ''}`}>{flyThrough ? 'ON' : 'OFF'}</span>
          </button>
        </div>
      )}
    </div>
  );
}

export default MapToolsPanel;

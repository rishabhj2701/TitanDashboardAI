import { useState, useCallback, useRef, useEffect } from 'react';
import { MAPBOX_TOKEN } from '../config';

interface MapSearchBarProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
}

interface GeoResult {
  id: string;
  place_name: string;
  center: [number, number];
}

function MapSearchBar({ mapRef }: MapSearchBarProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<GeoResult[]>([]);
  const [open, setOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const search = useCallback((q: string) => {
    if (q.length < 2) { setResults([]); return; }
    fetch(`https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(q)}.json?access_token=${MAPBOX_TOKEN}&limit=5&country=us`)
      .then(r => r.json())
      .then(data => {
        const features = (data.features || []) as GeoResult[];
        setResults(features);
        setOpen(features.length > 0);
      })
      .catch(() => setResults([]));
  }, []);

  const handleChange = useCallback((value: string) => {
    setQuery(value);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => search(value), 300);
  }, [search]);

  const handleSelect = useCallback((result: GeoResult) => {
    const map = mapRef.current;
    if (!map) return;
    map.flyTo({ center: result.center, zoom: 14, duration: 2000 });
    setQuery(result.place_name);
    setOpen(false);
    setResults([]);
  }, [mapRef]);

  return (
    <div className="map-search-wrapper" ref={wrapperRef}>
      <div className="map-search-input-row">
        <svg className="map-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
        </svg>
        <input
          className="map-search-input"
          type="text"
          placeholder="Search location..."
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => { if (results.length > 0) setOpen(true); }}
        />
        {query && (
          <button className="map-search-clear" onClick={() => { setQuery(''); setResults([]); setOpen(false); }}>&#x2715;</button>
        )}
      </div>
      {open && results.length > 0 && (
        <div className="map-search-results">
          {results.map((r) => (
            <button key={r.id} className="map-search-result-item" onClick={() => handleSelect(r)}>
              {r.place_name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default MapSearchBar;

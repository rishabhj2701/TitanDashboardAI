import { useState, useCallback } from 'react';
import { ROAD_LEGEND_BY_MODE, type RoadColorMode } from '../../features/map/roadColorMode';

type AreaBBox = {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
};

type Bookmark = {
  id: string;
  name: string;
  center: [number, number];
  zoom: number;
};

const BOOKMARKS_KEY = 'titan_map_bookmarks';

function loadBookmarks(): Bookmark[] {
  try {
    const raw = localStorage.getItem(BOOKMARKS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveBookmarks(bookmarks: Bookmark[]) {
  localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(bookmarks));
}

type MapLegendProps = {
  cvRoadLoading: boolean;
  hoverCoord: [number, number] | null;
  areaBBox: AreaBBox | null;
  mapRef: React.RefObject<mapboxgl.Map | null>;
  roadColorMode?: RoadColorMode;
};

function MapLegend({ cvRoadLoading, hoverCoord, areaBBox, mapRef, roadColorMode = 'vehicle-count' }: MapLegendProps) {
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>(loadBookmarks);
  const legend = ROAD_LEGEND_BY_MODE[roadColorMode];

  const handleSaveBookmark = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    const name = prompt('Bookmark name:');
    if (!name?.trim()) return;
    const center = map.getCenter();
    const zoom = map.getZoom();
    const bm: Bookmark = {
      id: `bm-${Date.now()}`,
      name: name.trim(),
      center: [center.lng, center.lat],
      zoom,
    };
    const updated = [...bookmarks, bm];
    setBookmarks(updated);
    saveBookmarks(updated);
  }, [mapRef, bookmarks]);

  const handleFlyToBookmark = useCallback((bm: Bookmark) => {
    const map = mapRef.current;
    if (!map) return;
    map.flyTo({ center: bm.center, zoom: bm.zoom, duration: 1500 });
  }, [mapRef]);

  const handleDeleteBookmark = useCallback((id: string) => {
    const updated = bookmarks.filter(b => b.id !== id);
    setBookmarks(updated);
    saveBookmarks(updated);
  }, [bookmarks]);

  return (
    <div className="map-legend">
      <div className="legend-title">Connected Vehicle Data</div>
      {cvRoadLoading && (
        <div className="legend-status">Loading road speeds...</div>
      )}

      <div className="legend-gradient-bar">
        <div className="legend-gradient-track">
          {legend.gradient.map((segment) => (
            <div
              key={`${segment.color}-${segment.flex}`}
              className="legend-gradient-segment"
              style={{ background: segment.color, flex: segment.flex }}
            />
          ))}
        </div>
        <div className="legend-gradient-labels">
          {legend.labels.map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
      </div>
      {legend.items.map((item) => (
        <div className="legend-item" key={`${item.color}-${item.label}`}>
          <div className="legend-color" style={{ backgroundColor: item.color }}></div>
          <span>{item.label}</span>
        </div>
      ))}

      {/* Bookmarks section */}
      <div className="legend-section-divider" />
      <button className="legend-section-toggle" onClick={() => setShowBookmarks(!showBookmarks)}>
        Bookmarks {showBookmarks ? '\u25B2' : '\u25BC'}
      </button>
      {showBookmarks && (
        <div className="legend-bookmarks">
          {bookmarks.length === 0 && (
            <div className="legend-bookmark-empty">No bookmarks saved</div>
          )}
          {bookmarks.map(bm => (
            <div key={bm.id} className="legend-bookmark-item">
              <button className="legend-bookmark-name" onClick={() => handleFlyToBookmark(bm)} title={`Fly to ${bm.name}`}>
                {bm.name}
              </button>
              <button className="legend-bookmark-delete" onClick={() => handleDeleteBookmark(bm.id)} title="Delete">&#x2715;</button>
            </div>
          ))}
          <button className="legend-bookmark-add" onClick={handleSaveBookmark}>+ Save current view</button>
        </div>
      )}

      <div className="map-coords">
        <div className="map-coords-row">
          <span>Lon</span>
          <strong>{hoverCoord ? hoverCoord[0].toFixed(5) : '\u2014'}</strong>
        </div>
        <div className="map-coords-row">
          <span>Lat</span>
          <strong>{hoverCoord ? hoverCoord[1].toFixed(5) : '\u2014'}</strong>
        </div>
        {areaBBox && (
          <div className="map-coords-row bbox">
            <span>Box</span>
            <strong>
              {areaBBox.minLon.toFixed(4)},{areaBBox.minLat.toFixed(4)} →
              {areaBBox.maxLon.toFixed(4)},{areaBBox.maxLat.toFixed(4)}
            </strong>
          </div>
        )}
      </div>
    </div>
  );
}

export default MapLegend;

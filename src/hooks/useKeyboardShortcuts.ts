import { useEffect } from 'react';
// import type mapboxgl from 'mapbox-gl';

export interface UseKeyboardShortcutsParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapStats: { total: number };
  getMapDataForRender: () => any[];
  clearAreaDraw: () => void;
  captureScreenshot: () => void;
  onToggleShortcuts?: () => void;
}

export const useKeyboardShortcuts = ({
  mapRef,
  mapStats,
  getMapDataForRender,
  clearAreaDraw,
  captureScreenshot,
  onToggleShortcuts,
}: UseKeyboardShortcutsParams) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in input fields
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }

      const map = mapRef.current;

      switch (e.key) {
        case 'Escape':
          // Clear selections and popups
          clearAreaDraw();
          document.querySelectorAll('.deck-crash-popup').forEach(el => el.remove());
          break;

        case 's':
        case 'S':
          // Screenshot (Ctrl+S or Cmd+S)
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            captureScreenshot();
          }
          break;

        case 'r':
        case 'R':
          // Reset view to default
          if (!e.ctrlKey && !e.metaKey) {
            map?.flyTo({ center: [-92.5, 38.5], zoom: 6.5 });
          }
          break;

        case '+':
        case '=':
          // Zoom in
          if (map) {
            map.zoomIn();
          }
          break;

        case '-':
        case '_':
          // Zoom out
          if (map) {
            map.zoomOut();
          }
          break;

        case 'f':
        case 'F':
          // Fit to data bounds
          if (map && mapStats.total > 0) {
            const data = getMapDataForRender();
            if (data.length > 0) {
              const lngs = data.map(d => d.longitude).filter(Boolean) as number[];
              const lats = data.map(d => d.latitude).filter(Boolean) as number[];
              if (lngs.length > 0 && lats.length > 0) {
                const bounds: [[number, number], [number, number]] = [
                  [Math.min(...lngs), Math.min(...lats)],
                  [Math.max(...lngs), Math.max(...lats)]
                ];
                map.fitBounds(bounds, { padding: 50 });
              }
            }
          }
          break;

        case '?':
          // Toggle keyboard shortcuts overlay
          if (onToggleShortcuts) {
            onToggleShortcuts();
          }
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [mapRef, clearAreaDraw, captureScreenshot, getMapDataForRender, mapStats.total, onToggleShortcuts]);
};

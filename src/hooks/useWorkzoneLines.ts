import { useEffect } from 'react';
import mapboxgl from 'mapbox-gl';
import { buildWorkzoneLineCollection } from '../features/map/featureBuilders';
import type { WorkzoneLine } from '../features/map/types';
import { formatDateTimeHuman } from '../utils/dateTime';

export interface UseWorkzoneLinesParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  workzoneLines: WorkzoneLine[];
  syncLayerVisibility: () => void;
}

export const useWorkzoneLines = (params: UseWorkzoneLinesParams) => {
  const { mapRef, mapReady, workzoneLines, syncLayerVisibility } = params;

  useEffect(() => {
    if (!mapReady || !mapRef.current) {
      return;
    }

    const map = mapRef.current;

    const updateWorkzoneLines = () => {
      const geojson = buildWorkzoneLineCollection(workzoneLines);

      // Create source if it doesn't exist
      if (!map.getSource('workzone-lines')) {
        map.addSource('workzone-lines', {
          type: 'geojson',
          data: geojson,
        });
      } else {
        const source = map.getSource('workzone-lines') as mapboxgl.GeoJSONSource;
        source.setData(geojson);
      }

      // Create layer if it doesn't exist
      if (!map.getLayer('workzone-lines')) {
        map.addLayer({
          id: 'workzone-lines',
          type: 'line',
          source: 'workzone-lines',
          layout: {
            'line-cap': 'round',
            'line-join': 'round',
          },
          paint: {
            'line-color': '#ffd54f',
            'line-width': 4,
            'line-opacity': 0.9,
          },
        });

        // Add hover handlers after creating layer
        const popup = new mapboxgl.Popup({
          closeButton: false,
          closeOnClick: false,
          className: 'app-popup',
          offset: [0, -15],
          anchor: 'bottom',
        });

        const handleLineMouseEnter = (e: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] }) => {
          map.getCanvas().style.cursor = 'pointer';
          if (!e.features || e.features.length === 0) return;
          const feature = e.features[0];
          const props = feature.properties as any;
          const roadName = props?.roadName || 'Unknown';
          const status = (props?.status || 'Unknown').toUpperCase();
          const start = formatDateTimeHuman(props?.startDate, 'N/A');
          const end = formatDateTimeHuman(props?.endDate, 'N/A');
          const description = props?.description || '';
          const html = `
            <div style="
              padding: 0;
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
              background: linear-gradient(135deg, rgba(40, 35, 5, 0.98), rgba(60, 45, 10, 0.98));
              color: #fff;
              border: 1px solid rgba(255, 213, 79, 0.6);
              border-radius: 10px;
              overflow: hidden;
              box-shadow: 0 8px 24px rgba(255, 213, 79, 0.4);
              min-width: 220px;
            ">
              <div style="
                background: rgba(255, 213, 79, 0.15);
                padding: 10px 12px;
                border-bottom: 1px solid rgba(255, 213, 79, 0.3);
                display: flex;
                align-items: center;
                gap: 10px;
              ">
                <div style="font-size: 22px;">🚧</div>
                <div>
                  <div style="font-size: 13px; font-weight: 700; color: #ffd54f;">WORKZONE</div>
                  <div style="font-size: 10px; letter-spacing: 1px; color: rgba(255, 255, 255, 0.6);">${status}</div>
                </div>
              </div>
              <div style="padding: 10px 12px;">
                <div style="font-size: 10px; text-transform: uppercase; color: rgba(255, 255, 255, 0.5); margin-bottom: 4px;">Road</div>
                <div style="font-size: 12px; font-weight: 600; color: #ffe082; margin-bottom: 8px;">${roadName}</div>
                <div style="font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-bottom: 4px;">
                  <strong>Start:</strong> ${start}
                </div>
                <div style="font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-bottom: 6px;">
                  <strong>End:</strong> ${end}
                </div>
                ${description ? `<div style="font-size: 10px; color: rgba(255, 255, 255, 0.75); border-top: 1px solid rgba(255, 213, 79, 0.2); padding-top: 6px;">${description}</div>` : ''}
              </div>
            </div>
          `;
          popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
        };

        const handleLineMouseLeave = () => {
          map.getCanvas().style.cursor = '';
          popup.remove();
        };

        map.on('mouseenter', 'workzone-lines', handleLineMouseEnter);
        map.on('mouseleave', 'workzone-lines', handleLineMouseLeave);
      }

      syncLayerVisibility();
      
      // Fit bounds to workzone lines
      if (workzoneLines.length > 0) {
        try {
          const bounds = new mapboxgl.LngLatBounds();
          let valid = 0;
          workzoneLines.forEach((line) => {
            line.coordinates.forEach(([lon, lat]) => {
              if (Number.isFinite(lon) && Number.isFinite(lat) &&
                  lon >= -180 && lon <= 180 && lat >= -90 && lat <= 90) {
                bounds.extend([lon, lat]);
                valid += 1;
              }
            });
          });
          if (valid > 0) {
            map.fitBounds(bounds, { padding: 60, maxZoom: 11 });
          }
        } catch (error) {
          console.error('[App] Error fitting bounds:', error);
        }
      }
    };

    // If style is already loaded, update immediately
    if (map.isStyleLoaded()) {
      updateWorkzoneLines();
    } else {
      // Otherwise, wait for style to load
      map.once('styledata', () => {
        updateWorkzoneLines();
      });
    }
  }, [workzoneLines, mapReady, syncLayerVisibility, mapRef]);
};

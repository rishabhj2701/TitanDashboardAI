import { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import { createDeckOverlay, removeDeckOverlay } from '../services/deckVisualization';
import { buildFeatureCollection } from '../features/map/featureBuilders';
import type { ProcessedVehicleData } from '../services/dataLoader';
import { formatDateTimeHuman } from '../utils/dateTime';
import { buildCrashPopupHtml } from '../features/map/popupHtml';

export interface UseMapDataUpdatesParams {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
  mapInitialized: boolean;
  mapDataset: ProcessedVehicleData[];
  mapOverrideActive: boolean;
  areaPolygon: GeoJSON.Polygon | null;
  getMapDataForRender: () => ProcessedVehicleData[];
  syncLayerVisibility: () => void;
  rebuildVehicleLayers: (map: mapboxgl.Map, geojson: GeoJSON.FeatureCollection<GeoJSON.Point, any>) => void;
  popupRef: React.MutableRefObject<mapboxgl.Popup | null>;
  clickPopupRef: React.MutableRefObject<mapboxgl.Popup | null>;
}

export const useMapDataUpdates = (params: UseMapDataUpdatesParams) => {
  const {
    mapRef,
    mapReady,
    mapInitialized,
    mapDataset,
    mapOverrideActive,
    areaPolygon,
    getMapDataForRender,
    syncLayerVisibility,
    rebuildVehicleLayers,
    popupRef,
    clickPopupRef,
  } = params;

  const accLogRef = useRef(0);
  const crashPopupRef = useRef<HTMLElement | null>(null);
  const drawInteractionBlockedRef = useRef(false);

  useEffect(() => {
    if (!mapRef.current || !mapReady || !mapInitialized) return;
  
    const map = mapRef.current;
    const deckHoverEventName = 'deck-point-hover-active';
    const hoverClearEventName = 'map-hover-clear';
    const drawInteractionEventName = 'area-draw-interaction';
    const emitDeckPointHover = (active: boolean) => {
      window.dispatchEvent(new CustomEvent(deckHoverEventName, { detail: { active } }));
    };
    let updateMapDataFn: (() => void) | null = null;
    const clearDeckCrashPopups = () => {
      document.querySelectorAll('.deck-crash-popup').forEach((el) => el.remove());
    };
    const removeActiveCrashPopup = () => {
      if (crashPopupRef.current && crashPopupRef.current.isConnected) {
        crashPopupRef.current.remove();
      }
      crashPopupRef.current = null;
      clearDeckCrashPopups();
    };
    const clearHoverUiGlobal = () => {
      emitDeckPointHover(false);
      map.getCanvas().style.cursor = '';
      popupRef.current?.remove();
    };
    const handleMapMouseOut = () => {
      clearHoverUiGlobal();
    };
    const handleExternalHoverClear = () => {
      clearHoverUiGlobal();
      clickPopupRef.current?.remove();
      removeActiveCrashPopup();
    };
    const isDrawInteractionBlocked = () => {
      if (drawInteractionBlockedRef.current) return true;
      const classList = map.getContainer()?.classList;
      if (!classList) return false;
      return (
        classList.contains('mode-draw_polygon') ||
        classList.contains('mode-draw_line_string') ||
        classList.contains('mode-draw_point')
      );
    };
    const handleDrawInteractionEvent = (event: Event) => {
      const custom = event as CustomEvent<{ active?: boolean }>;
      drawInteractionBlockedRef.current = Boolean(custom.detail?.active);
      if (drawInteractionBlockedRef.current) {
        emitDeckPointHover(false);
        map.getCanvas().style.cursor = '';
        popupRef.current?.remove();
        clickPopupRef.current?.remove();
        removeActiveCrashPopup();
        removeDeckOverlay();
      } else {
        updateMapDataFn?.();
      }
    };
    map.on('mouseout', handleMapMouseOut);
    window.addEventListener(hoverClearEventName, handleExternalHoverClear as EventListener);
    window.addEventListener(drawInteractionEventName, handleDrawInteractionEvent as EventListener);

    const updateMapData = () => {
      // Do not block updates on isStyleLoaded()/loaded() because those can stay false while map is still interactive.
      if (!map.getStyle()) {
        console.debug('[Map] style unavailable; skipping update pass');
        return;
      }

      if (isDrawInteractionBlocked()) {
        removeActiveCrashPopup();
        emitDeckPointHover(false);
        removeDeckOverlay();
        map.getCanvas().style.cursor = '';
        popupRef.current?.remove();
        clickPopupRef.current?.remove();
        syncLayerVisibility();
        return;
      }

      const dataForMap = getMapDataForRender();
      if (dataForMap.length === 0) {
        removeActiveCrashPopup();
        emitDeckPointHover(false);
        removeDeckOverlay();
        syncLayerVisibility();
        console.debug('[Map] no point data to render; showing road layers only');
        return;
      }
      const now = Date.now();
      if (now - accLogRef.current > 4000) {
        accLogRef.current = now;
        const nonzeroAcc = dataForMap.find((p) =>
          Math.abs(p.acceleration?.x ?? 0) > 0.0001 ||
          Math.abs(p.acceleration?.y ?? 0) > 0.0001
        );
        const sample = nonzeroAcc ?? dataForMap[0];
        if (sample) {
          console.debug('[Map] accel sample', {
            totalPoints: dataForMap.length,
            sampleId: sample.id,
            accX: sample.acceleration?.x ?? null,
            accY: sample.acceleration?.y ?? null,
            speed: sample.speed,
            type: sample.type,
          });
        }
      }
      const geojson = buildFeatureCollection(dataForMap);
      let source = map.getSource('vehicle-data') as mapboxgl.GeoJSONSource | undefined;
      
      if (!source) {
        console.warn('⚠️ Source not found, rebuilding layers before update');
        rebuildVehicleLayers(map, geojson);
        source = map.getSource('vehicle-data') as mapboxgl.GeoJSONSource | undefined;
      }

      try {
        if (source) {
          source.setData(geojson);
        }
        console.debug(`[Map] updated map with ${geojson.features.length} points`);
        syncLayerVisibility();

        // Initialize popups if not already created
        if (!popupRef.current) {
          popupRef.current = new mapboxgl.Popup({
            closeButton: false,
            closeOnClick: false,
            className: 'app-popup',
            offset: [0, -15],
            anchor: 'bottom',
          });
        }
        if (!clickPopupRef.current) {
          clickPopupRef.current = new mapboxgl.Popup({
            closeButton: true,
            closeOnClick: true,
            className: 'app-popup-clickable',
          });
        }

        const hoverPopup = popupRef.current;
        const clickPopup = clickPopupRef.current;

        // Helper to build popup HTML for deck.gl hover/click
        const buildDeckPopupHtml = (d: any, forClick: boolean = false, buttonId?: string): string => {
          const type = (d.type ?? '').toLowerCase();
          const coordinates: [number, number] = [d.longitude, d.latitude];

          if (type === 'crash') {
            return buildCrashPopupHtml(d, coordinates, forClick && !!buttonId, buttonId);
          } else if (type === 'hardbrake') {
            const firstFinite = (...values: Array<unknown>) => {
              for (const value of values) {
                const numeric = typeof value === 'number' ? value : Number(value);
                if (Number.isFinite(numeric)) return numeric;
              }
              return Number.NaN;
            };
            const roadLabel = d.roadName || d.road_name || d.road || d.label || d.ref || d.road_segment_id || 'Unknown road';
            const decelRaw = firstFinite(
              d.decelerationG,
              d.acc_x,
              d.accX,
              d.acceleration?.x
            );
            const decel = Number.isFinite(decelRaw) ? Math.abs(decelRaw) : Number.NaN;
            const decelLabel = Number.isFinite(decel) && decel > 0 ? `${decel.toFixed(2)} g` : 'N/A';
            const speedNow = firstFinite(d.speed, d.speed_mph, d.Speed);
            const speedNowLabel = Number.isFinite(speedNow) ? `${speedNow.toFixed(0)} mph` : 'N/A';
            const speedLimit = firstFinite(
              d.speedLimit,
              d.SpeedLimitMPH,
              d.SpeedLimit,
              d.speed_limit_mph,
              d.speed_limit
            );
            const speedLimitLabel = Number.isFinite(speedLimit) && speedLimit > 0 ? `${speedLimit.toFixed(0)} mph` : 'N/A';
            const eventTsLabel = formatDateTimeHuman(
              d.eventDate ?? d.timestamp ?? d.ts ?? d.accident_ts,
              'N/A'
            );
            return `<div style="padding:0;font-family:-apple-system,sans-serif;background:linear-gradient(135deg,rgba(20,10,40,0.98),rgba(35,10,60,0.98));color:#fff;border:1px solid rgba(155,93,229,0.5);border-radius:8px;overflow:hidden;box-shadow:0 8px 32px rgba(155,93,229,0.4);min-width:220px;">
              <div style="background:rgba(155,93,229,0.2);padding:10px 12px;border-bottom:1px solid rgba(155,93,229,0.3);display:flex;align-items:center;gap:10px;">
                <div style="font-size:24px;">🛑</div>
                <div><div style="font-size:14px;font-weight:700;color:#f9f871;">HARD BRAKE</div><div style="font-size:9px;color:rgba(255,255,255,0.6);">${roadLabel}</div></div>
              </div>
              <div style="padding:10px 12px;font-size:11px;color:#e0e7ff;">
                <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,166,0,0.4);padding:6px 8px;border-radius:6px;margin-bottom:8px;"><span>Deceleration</span><strong style="font-size:13px;">${decelLabel}</strong></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:4px;"><span style="opacity:0.7;">Speed</span><strong>${speedNowLabel}</strong></div>
                <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:4px;"><span style="opacity:0.7;">Limit</span><strong>${speedLimitLabel}</strong></div>
                <div style="font-size:9px;color:rgba(255,255,255,0.55);margin-top:6px;padding-top:6px;border-top:1px solid rgba(155,93,229,0.2);">${eventTsLabel}</div>
              </div>
            </div>`;
          } else {
            // CV point
            const speedVal = d.speed ?? 0;
            const speedLimitVal = d.speedLimit ?? 0;
            const hasLimit = Number.isFinite(speedLimitVal) && speedLimitVal > 0;
            const delta = hasLimit ? speedVal - speedLimitVal : 0;
            let statusColor = '#2e7d32';
            let statusLabel = 'Within ±10 mph of limit';
            if (!hasLimit) {
              statusColor = '#9e9e9e';
              statusLabel = 'No speed limit';
            } else if (delta <= -10) {
              statusColor = '#e53935';
              statusLabel = 'More than 10 mph below limit';
            } else if (delta >= 10) {
              statusColor = '#8b0000';
              statusLabel = 'More than 10 mph above limit';
            }
            return `<div style="padding:0;font-family:-apple-system,sans-serif;background:linear-gradient(135deg,rgba(10,14,39,0.98),rgba(20,25,50,0.98));color:#fff;border:1px solid ${statusColor};border-left:5px solid ${statusColor};border-radius:8px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.6);min-width:220px;">
              <div style="background:rgba(255,255,255,0.04);padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;gap:8px;">
                <div style="font-size:24px;font-weight:700;color:${statusColor};">${speedVal.toFixed(0)}</div>
                <div><div style="font-size:10px;color:rgba(255,255,255,0.6);">MPH</div><div style="font-size:9px;color:${statusColor};">${statusLabel}</div></div>
              </div>
              <div style="padding:10px 12px;font-size:11px;color:#ccd6f6;">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;"><span style="color:#64ffda;">📍</span><span>${d.roadName || 'Unknown'}</span></div>
                <div style="font-size:10px;color:rgba(255,255,255,0.7);">Limit: ${speedLimitVal > 0 ? `${speedLimitVal.toFixed(0)} MPH` : 'N/A'}</div>
              </div>
            </div>`;
          }
        };

        // Create deck.gl overlay for points visualization
        createDeckOverlay(map, dataForMap, {
          onHover: (info, _event) => {
            if (crashPopupRef.current && crashPopupRef.current.isConnected) {
              emitDeckPointHover(true);
              hoverPopup.remove();
              return;
            }
            if (!info?.object) {
              emitDeckPointHover(false);
              map.getCanvas().style.cursor = '';
              hoverPopup.remove();
              return;
            }
            emitDeckPointHover(true);
            map.getCanvas().style.cursor = 'pointer';
            const d = info.object as any;
            const coordinates: [number, number] = [d.longitude, d.latitude];
            const html = buildDeckPopupHtml(d, false);
            hoverPopup.setLngLat(coordinates).setHTML(html).addTo(map);
          },
          onClick: (info, _event) => {
            console.log('[deck.gl] Click detected, info:', info);
            if (!info?.object) {
              emitDeckPointHover(false);
              console.log('[deck.gl] No object picked');
              return;
            }
            emitDeckPointHover(true);
            const d = info.object as any;
            const coordinates: [number, number] = [d.longitude, d.latitude];
            const type = (d.type ?? '').toLowerCase();
            console.log('[deck.gl] Clicked object type:', type, 'original:', d.type);

            if (type === 'crash') {
              try {
                console.log('[deck.gl] Creating crash popup at:', coordinates);
                const buttonId = `analyze-crash-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
                console.log('[deck.gl] Calling buildDeckPopupHtml...');
                const html = buildDeckPopupHtml(d, true, buttonId);
                console.log('[deck.gl] Popup HTML created, buttonId:', buttonId);

                // Remove hover popup first to avoid conflicts
                hoverPopup.remove();
                clickPopup.remove();
                removeActiveCrashPopup();

                // Directly inject popup HTML into DOM as a workaround
                const popupContainer = document.createElement('div');
                popupContainer.className = 'deck-crash-popup';
                popupContainer.style.cssText = `
                  position: fixed;
                  z-index: 9999;
                  pointer-events: auto;
                `;
                popupContainer.innerHTML = html;
                document.body.appendChild(popupContainer);

                // Position popup near click location
                const canvas = map.getCanvas();
                const rect = canvas.getBoundingClientRect();
                const point = map.project(coordinates);
                popupContainer.style.left = `${rect.left + point.x - 110}px`;
                popupContainer.style.top = `${rect.top + point.y - 250}px`;
                crashPopupRef.current = popupContainer;

                console.log('[deck.gl] Popup injected into DOM');
                let removeOnOutsideClick: ((e: MouseEvent) => void) | null = null;
                const removePopup = () => {
                  if (removeOnOutsideClick) {
                    document.removeEventListener('click', removeOnOutsideClick);
                    removeOnOutsideClick = null;
                  }
                  popupContainer.remove();
                  if (crashPopupRef.current === popupContainer) {
                    crashPopupRef.current = null;
                  }
                };

                // Add click handler for analyze button
                const analyzeButton = popupContainer.querySelector(`#${buttonId}`) as HTMLButtonElement | null;
                if (analyzeButton) {
                  console.log('[deck.gl] Analyze button found');
                  analyzeButton.onclick = (evt) => {
                    evt.preventDefault();
                    evt.stopPropagation();
                    console.log('[deck.gl] Analyze button clicked');
                    window.dispatchEvent(new CustomEvent('crash-analyze', {
                      detail: {
                        crash_lat: coordinates[1],
                        crash_lon: coordinates[0],
                        crash_ts: d.timestamp || null,
                        accident_date: d.accident_date ?? null,
                        accident_time: d.accident_time ?? null,
                        severity: d.severity ?? d.crashSeverity ?? null,
                        road_segment_id: d.road_segment_id ?? null,
                        crash_id: d.primary_id || d.hp_acc_image_no || d.id || null,
                        distance_m: 200,
                        window_minutes: 60,
                      },
                    }));
                    removePopup();
                  };
                }

                // Add close button handler
                const closeBtn = document.createElement('button');
                closeBtn.innerHTML = '×';
                closeBtn.style.cssText = `
                  position: absolute;
                  top: 5px;
                  right: 8px;
                  background: none;
                  border: none;
                  color: white;
                  font-size: 20px;
                  cursor: pointer;
                  z-index: 10;
                `;
                closeBtn.onclick = () => removePopup();
                popupContainer.querySelector('div')?.appendChild(closeBtn);

                // Remove on outside click
                removeOnOutsideClick = (e: MouseEvent) => {
                  if (!popupContainer.contains(e.target as Node)) {
                    removePopup();
                  }
                };
                setTimeout(() => {
                  if (removeOnOutsideClick) {
                    document.addEventListener('click', removeOnOutsideClick);
                  }
                }, 100);
              } catch (err) {
                console.error('[deck.gl] Error creating crash popup:', err);
              }
            } else {
              removeActiveCrashPopup();
              const html = buildDeckPopupHtml(d, false);
              clickPopup.setLngLat(coordinates).setHTML(html).addTo(map);
            }
          },
        }, { showCvPoints: true, showCrashes: true, showHardBraking: true });

        // Fit bounds if we have data (skip massive datasets to avoid zooming out)
        if (geojson.features.length > 0) {
          if (areaPolygon) {
            const bounds = new mapboxgl.LngLatBounds();
            areaPolygon.coordinates.forEach((ring) => {
              ring.forEach(([lon, lat]) => {
                if (Number.isFinite(lon) && Number.isFinite(lat)) {
                  bounds.extend([lon, lat]);
                }
              });
            });
            if (!bounds.isEmpty()) {
              const lonSpan = Math.abs(bounds.getEast() - bounds.getWest());
              const latSpan = Math.abs(bounds.getNorth() - bounds.getSouth());
              let minZoom = 11;
              if (lonSpan < 0.02 && latSpan < 0.02) {
                minZoom = 13;
              } else if (lonSpan < 0.05 && latSpan < 0.05) {
                minZoom = 12;
              }
              const camera = map.cameraForBounds(bounds, { padding: 70, maxZoom: 15 });
              if (camera && typeof camera.zoom === 'number') {
                camera.zoom = Math.max(camera.zoom, minZoom);
                map.easeTo(camera);
              } else {
                map.fitBounds(bounds, { padding: 70, maxZoom: 15 });
              }
            }
          } else if (dataForMap.length <= 5000) {
            const bounds = new mapboxgl.LngLatBounds();
            let valid = 0;
            dataForMap.forEach((point) => {
              if (Number.isFinite(point.longitude) && Number.isFinite(point.latitude)) {
                bounds.extend([point.longitude, point.latitude]);
                valid += 1;
              }
            });
            if (valid > 0) {
              map.fitBounds(bounds, { padding: 50, maxZoom: 12 });
            }
          }
        }
      } catch (error) {
        console.error('❌ Failed to update map data:', error);
      }
    };
    updateMapDataFn = updateMapData;

    updateMapData();

    // Cleanup deck.gl overlay on unmount
    return () => {
      map.off('mouseout', handleMapMouseOut);
      window.removeEventListener(hoverClearEventName, handleExternalHoverClear as EventListener);
      window.removeEventListener(drawInteractionEventName, handleDrawInteractionEvent as EventListener);
      drawInteractionBlockedRef.current = false;
      emitDeckPointHover(false);
      removeActiveCrashPopup();
      popupRef.current?.remove();
      clickPopupRef.current?.remove();
      removeDeckOverlay();
    };
  }, [mapDataset, mapReady, mapInitialized, mapOverrideActive, getMapDataForRender, syncLayerVisibility, areaPolygon, rebuildVehicleLayers, popupRef, clickPopupRef, mapRef]);
};

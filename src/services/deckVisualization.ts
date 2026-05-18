/**
 * Deck.gl Visualization Service
 * Primary visualization layer for CV points, crashes, and hard braking events
 * Replaces Mapbox circle layers for better performance with large datasets
 * Updated: Simple point rendering without animations
 */

import { ScatterplotLayer } from '@deck.gl/layers';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Map as MapboxMap, IControl as MapboxIControl } from 'mapbox-gl';
import type { ProcessedVehicleData } from './dataLoader';

// Type for deck.gl picking info
interface PickingInfo {
  object?: ProcessedVehicleData;
  coordinate?: [number, number];
  x: number;
  y: number;
  index: number;
  layer?: { id: string };
}

// Store reference to the overlay for cleanup
let deckOverlay: MapboxOverlay | null = null;
let currentMap: MapboxMap | null = null;
let zoomCallback: (() => void) | null = null;

// Callbacks for hover and click events
let hoverCallback: ((info: PickingInfo | null, event: MouseEvent) => void) | null = null;
let clickCallback: ((info: PickingInfo, event: MouseEvent) => void) | null = null;

const removeLeakedDeckControls = (map: MapboxMap): void => {
  try {
    const controls = ((map as any)?._controls ?? []) as any[];
    for (const ctrl of controls) {
      const name = ctrl?.constructor?.name ?? '';
      if (name === 'MapboxOverlay') {
        try {
          map.removeControl(ctrl as MapboxIControl);
        } catch {
          // ignore leaked-control cleanup errors
        }
      }
    }
  } catch {
    // ignore private API access errors
  }
};

/**
 * Get color for CV points based on speed delta
 * - Green: Within ±10 mph of limit
 * - Red: More than 10 mph below limit
 * - Dark Red: More than 10 mph above limit
 */
const getCvPointColor = (d: ProcessedVehicleData): [number, number, number, number] => {
  const speed = d.speed ?? 0;
  const speedLimit = d.speedLimit ?? speed;
  const delta = speed - speedLimit;

  if (delta < -10) {
    // More than 10 mph below limit - Red
    return [229, 57, 53, 240]; // #e53935
  } else if (delta > 10) {
    // More than 10 mph above limit - Dark Red
    return [139, 0, 0, 240]; // #8b0000
  } else {
    // Within ±10 mph of limit - Green
    return [46, 125, 50, 240]; // #2e7d32
  }
};

/**
 * Get color for hard braking events based on deceleration
 * Purple/magenta gradient for distinction from other points
 */
const getHardBrakeColor = (d: ProcessedVehicleData): [number, number, number, number] => {
  const accX = Math.abs(d.acceleration?.x ?? 0);

  if (accX >= 0.8) return [156, 39, 176, 255];   // #9c27b0 - extreme (deep purple)
  if (accX >= 0.6) return [171, 71, 188, 255];   // #ab47bc - severe
  if (accX >= 0.4) return [186, 104, 200, 255];  // #ba68c8 - moderate
  if (accX >= 0.2) return [206, 147, 216, 255];  // #ce93d8 - mild
  return [225, 190, 231, 255];                    // #e1bee7 - light
};

/**
 * Get color for crash points based on severity
 */
const getCrashColor = (d: ProcessedVehicleData): [number, number, number, number] => {
  const severity = (d.severity ?? d.crashSeverity ?? '').toLowerCase();

  if (severity.includes('fatal')) {
    return [230, 90, 90, 210]; // red
  } else if (severity.includes('serious')) {
    return [240, 110, 100, 210]; // coral
  } else if (severity.includes('minor')) {
    return [245, 170, 80, 210]; // orange
  } else if (severity.includes('property')) {
    return [245, 200, 100, 210]; // amber
  }
  return [240, 110, 100, 210]; // default red
};

/**
 * Get point radius based on type and zoom
 */
const getPointRadius = (d: ProcessedVehicleData, zoom: number): number => {
  const type = d.type?.toLowerCase() ?? '';
  const isCrash = type === 'crash';
  const isHardBrake = type === 'hardbrake';

  // Smooth exponential scaling based on zoom
  // At zoom 6: tiny, at zoom 16: large
  const baseRadius = Math.pow(1.25, zoom - 8) * 2;

  // Clamp base radius
  const clampedBase = Math.max(1.5, Math.min(baseRadius, 12));

  // Crashes are larger and more prominent
  if (isCrash) return clampedBase * 2;
  // Hard brakes slightly larger
  if (isHardBrake) return clampedBase * 1.3;

  return clampedBase;
};

export interface DeckLayerCallbacks {
  onHover?: (info: PickingInfo | null, event: MouseEvent) => void;
  onClick?: (info: PickingInfo, event: MouseEvent) => void;
}

export interface DeckLayerVisibility {
  showCvPoints?: boolean;
  showCrashes?: boolean;
  showHardBraking?: boolean;
}

// Store current visibility settings
let currentVisibility: DeckLayerVisibility = {
  showCvPoints: true,
  showCrashes: true,
  showHardBraking: true,
};

/**
 * Create the deck.gl overlay with all point layers
 */
export const createDeckOverlay = (
  map: MapboxMap,
  data: ProcessedVehicleData[],
  callbacks?: DeckLayerCallbacks,
  visibility?: DeckLayerVisibility
): void => {
  // Update visibility settings
  currentVisibility = {
    showCvPoints: visibility?.showCvPoints ?? true,
    showCrashes: visibility?.showCrashes ?? true,
    showHardBraking: visibility?.showHardBraking ?? true,
  };
  console.log(`[deck.gl] Creating visualization with ${data.length} points`);

  // Remove existing overlay first (may use previous currentMap reference)
  removeDeckOverlay();
  // Remove any leaked overlays from prior buggy sessions.
  removeLeakedDeckControls(map);

  // Store references for the new overlay lifecycle
  currentMap = map;
  hoverCallback = callbacks?.onHover ?? null;
  clickCallback = callbacks?.onClick ?? null;

  if (data.length === 0) {
    console.log('[deck.gl] No data to visualize');
    return;
  }

  // Separate data by type
  const cvPoints = data.filter(d => {
    const type = (d.type ?? '').toLowerCase();
    return type !== 'crash' && type !== 'hardbrake';
  });

  const hardBrakePoints = data.filter(d => {
    const type = (d.type ?? '').toLowerCase();
    return type === 'hardbrake';
  });

  const crashPoints = data.filter(d => {
    const type = (d.type ?? '').toLowerCase();
    return type === 'crash';
  });

  console.log(`[deck.gl] Points breakdown: CV=${cvPoints.length}, HardBrake=${hardBrakePoints.length}, Crash=${crashPoints.length}`);

  const currentZoom = map.getZoom();

  // Create layers
  const layers = [];

  // CV Points Layer (bottom) - Small dots
  if (cvPoints.length > 0 && currentVisibility.showCvPoints) {
    layers.push(new ScatterplotLayer({
      id: 'deck-cv-points',
      data: cvPoints,
      pickable: true,
      opacity: 0.8,
      stroked: false,
      filled: true,
      radiusMinPixels: 3,
      radiusMaxPixels: 8,
      getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
      getRadius: 4,
      getFillColor: (d: ProcessedVehicleData) => getCvPointColor(d),
      parameters: {
        depthTest: false,
      },
    }));
  }

  // Hard Braking Layer (middle) - Purple with subtle glow
  if (hardBrakePoints.length > 0 && currentVisibility.showHardBraking) {
    // Glow layer (larger, more transparent)
    layers.push(new ScatterplotLayer({
      id: 'deck-hardbrake-glow',
      data: hardBrakePoints,
      pickable: false,
      opacity: 0.4,
      stroked: false,
      filled: true,
      radiusMinPixels: 6,
      radiusMaxPixels: 35,
      getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
      getRadius: (d: ProcessedVehicleData) => getPointRadius(d, currentZoom) * 1.8,
      getFillColor: (d: ProcessedVehicleData) => getHardBrakeColor(d),
      updateTriggers: {
        getRadius: [currentZoom],
      },
      parameters: {
        depthTest: false,
      },
    }));
    // Main layer
    layers.push(new ScatterplotLayer({
      id: 'deck-hardbrake-points',
      data: hardBrakePoints,
      pickable: true,
      opacity: 1,
      stroked: true,
      filled: true,
      radiusMinPixels: 4,
      radiusMaxPixels: 22,
      lineWidthUnits: 'pixels',
      lineWidthMinPixels: 0.5,
      lineWidthMaxPixels: 1.5,
      getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
      getRadius: (d: ProcessedVehicleData) => getPointRadius(d, currentZoom),
      getFillColor: (d: ProcessedVehicleData) => getHardBrakeColor(d),
      getLineColor: [255, 255, 255, 100],
      getLineWidth: 1,
      updateTriggers: {
        getRadius: [currentZoom],
      },
      parameters: {
        depthTest: false,
      },
    }));
  }

  // Crash Points Layer (top) - Larger, slightly brighter
  if (crashPoints.length > 0 && currentVisibility.showCrashes) {
    layers.push(new ScatterplotLayer({
      id: 'deck-crash-points',
      data: crashPoints,
      pickable: true,
      opacity: 0.85,
      stroked: false,
      filled: true,
      radiusMinPixels: 8,
      radiusMaxPixels: 20,
      getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
      getRadius: 10,
      getFillColor: (d: ProcessedVehicleData) => getCrashColor(d),
      parameters: {
        depthTest: false,
      },
    }));
  }

  // Create the overlay
  deckOverlay = new MapboxOverlay({
    layers,
    interleaved: false,
  });

  // Add to map
  map.addControl(deckOverlay as unknown as MapboxIControl);

  // Set up event handlers on the map canvas
  const canvas = map.getCanvas();

  const handleMouseMove = (event: MouseEvent) => {
    if (!deckOverlay) return;

    // Cast to any since pickObject is available but not in the types
    const pickInfo = (deckOverlay as any).pickObject({
      x: event.offsetX,
      y: event.offsetY,
      radius: 5,
    });

    if (hoverCallback) {
      hoverCallback(pickInfo as PickingInfo | null, event);
    }
  };

  const handleClick = (event: MouseEvent) => {
    if (!deckOverlay) return;

    // Don't process click if it's inside a popup (let popup handle it)
    const target = event.target as HTMLElement;
    if (target.closest('.mapboxgl-popup')) {
      return;
    }

    // Cast to any since pickObject is available but not in the types
    const pickInfo = (deckOverlay as any).pickObject({
      x: event.offsetX,
      y: event.offsetY,
      radius: 5,
    });

    if (pickInfo?.object && clickCallback) {
      clickCallback(pickInfo as PickingInfo, event);
    }
  };

  canvas.addEventListener('mousemove', handleMouseMove);
  canvas.addEventListener('click', handleClick);

  // Store cleanup references
  (deckOverlay as any)._customCleanup = () => {
    canvas.removeEventListener('mousemove', handleMouseMove);
    canvas.removeEventListener('click', handleClick);
  };

  // Update layers on zoom change
  const handleZoom = () => {
    if (!deckOverlay) return;
    const newZoom = map.getZoom();

    const updatedLayers = [];

    // CV Points - small dots
    if (cvPoints.length > 0 && currentVisibility.showCvPoints) {
      updatedLayers.push(new ScatterplotLayer({
        id: 'deck-cv-points',
        data: cvPoints,
        pickable: true,
        opacity: 0.8,
        stroked: false,
        filled: true,
        radiusMinPixels: 3,
        radiusMaxPixels: 8,
        getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
        getRadius: 4,
        getFillColor: (d: ProcessedVehicleData) => getCvPointColor(d),
        parameters: {
          depthTest: false,
        },
      }));
    }

    // Hard Braking - with glow
    if (hardBrakePoints.length > 0 && currentVisibility.showHardBraking) {
      updatedLayers.push(new ScatterplotLayer({
        id: 'deck-hardbrake-glow',
        data: hardBrakePoints,
        pickable: false,
        opacity: 0.4,
        stroked: false,
        filled: true,
        radiusMinPixels: 6,
        radiusMaxPixels: 35,
        getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
        getRadius: (d: ProcessedVehicleData) => getPointRadius(d, newZoom) * 1.8,
        getFillColor: (d: ProcessedVehicleData) => getHardBrakeColor(d),
        parameters: {
          depthTest: false,
        },
      }));
      updatedLayers.push(new ScatterplotLayer({
        id: 'deck-hardbrake-points',
        data: hardBrakePoints,
        pickable: true,
        opacity: 1,
        stroked: true,
        filled: true,
        radiusMinPixels: 4,
        radiusMaxPixels: 22,
        lineWidthUnits: 'pixels',
        lineWidthMinPixels: 0.5,
        lineWidthMaxPixels: 1.5,
        getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
        getRadius: (d: ProcessedVehicleData) => getPointRadius(d, newZoom),
        getFillColor: (d: ProcessedVehicleData) => getHardBrakeColor(d),
        getLineColor: [255, 255, 255, 100],
        getLineWidth: 1,
        parameters: {
          depthTest: false,
        },
      }));
    }

    // Crash Points - larger, slightly brighter
    if (crashPoints.length > 0 && currentVisibility.showCrashes) {
      updatedLayers.push(new ScatterplotLayer({
        id: 'deck-crash-points',
        data: crashPoints,
        pickable: true,
        opacity: 0.85,
        stroked: false,
        filled: true,
        radiusMinPixels: 8,
        radiusMaxPixels: 20,
        getPosition: (d: ProcessedVehicleData) => [d.longitude, d.latitude],
        getRadius: 10,
        getFillColor: (d: ProcessedVehicleData) => getCrashColor(d),
        parameters: {
          depthTest: false,
        },
      }));
    }

    deckOverlay.setProps({ layers: updatedLayers });
  };
  map.on('zoom', handleZoom);
  zoomCallback = handleZoom;

  console.log('[deck.gl] Visualization created successfully');
};

// Store current data for visibility updates
let currentData: ProcessedVehicleData[] = [];

/**
 * Update deck.gl overlay with new data
 */
export const updateDeckOverlay = (
  data: ProcessedVehicleData[],
  visibility?: DeckLayerVisibility
): void => {
  if (!currentMap) {
    console.warn('[deck.gl] No map reference, call createDeckOverlay first');
    return;
  }

  currentData = data;

  // Recreate with new data
  createDeckOverlay(currentMap, data, {
    onHover: hoverCallback ?? undefined,
    onClick: clickCallback ?? undefined,
  }, visibility);
};

/**
 * Update layer visibility without recreating overlay
 */
export const updateLayerVisibility = (visibility: DeckLayerVisibility): void => {
  if (!currentMap || currentData.length === 0) {
    return;
  }

  createDeckOverlay(currentMap, currentData, {
    onHover: hoverCallback ?? undefined,
    onClick: clickCallback ?? undefined,
  }, visibility);
};

/**
 * Remove deck.gl overlay from map
 */
export const removeDeckOverlay = (): void => {
  if (deckOverlay) {
    try {
      // Run custom cleanup first
      if ((deckOverlay as any)._customCleanup) {
        (deckOverlay as any)._customCleanup();
      }
      // Only try to remove from map if map is still valid
      const mapForOverlay: MapboxMap | null =
        (currentMap && typeof currentMap.removeControl === 'function')
          ? currentMap
          : (((deckOverlay as any)?._map ?? null) as MapboxMap | null);
      if (mapForOverlay && zoomCallback && typeof mapForOverlay.off === 'function') {
        try {
          mapForOverlay.off('zoom', zoomCallback);
        } catch (err) {
          console.debug('[deck.gl] Zoom listener cleanup skipped:', err);
        }
      }
      if (mapForOverlay && typeof mapForOverlay.removeControl === 'function') {
        mapForOverlay.removeControl(deckOverlay as unknown as MapboxIControl);
      }
    } catch (e) {
      // Silently handle cleanup errors (map may already be destroyed)
      console.debug('[deck.gl] Cleanup skipped (map destroyed):', e);
    }
    deckOverlay = null;
  }
  zoomCallback = null;
  currentMap = null;
};

/**
 * Check if deck.gl overlay is active
 */
export const isDeckOverlayActive = (): boolean => {
  return deckOverlay !== null;
};

/**
 * Get the current deck overlay (for advanced usage)
 */
export const getDeckOverlay = (): MapboxOverlay | null => {
  return deckOverlay;
};

// Animation functions (no-op since we removed glow effects)
export const startCrashAnimation = (): void => {};
export const stopCrashAnimation = (): void => {};
export const setAnimationEnabled = (_enabled: boolean): void => {};
export const isAnimationRunning = (): boolean => false;

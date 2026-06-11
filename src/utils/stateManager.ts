export interface AppState {
  generatedCharts: any[];
  vehicleData: any[];
  selectedFilters: any;
  mapState: {
    center: [number, number];
    zoom: number;
    mapDataset: any[]; // We'll exclude this from save
    workzoneLines: any[];
  };
  chatHistory: any[];
  timestamp: number;
}

const STATE_KEY = 'traffic_app_state_v2';
export function saveAppState(_state: AppState): void {
  try {
    // Temporarily disable persistence between sessions
    return;
  } catch (error) {
    console.error('Failed to save app state:', error);
  }
}

export function loadAppState(): AppState | null {
  try {
    return null; // Do not load persisted state
  } catch (error) {
    console.error('Failed to load app state:', error);
    return null;
  }
}

export function clearAppState(): void {
  try {
    localStorage.removeItem(STATE_KEY);
  } catch (error) {
    console.error('Failed to clear app state:', error);
  }
}

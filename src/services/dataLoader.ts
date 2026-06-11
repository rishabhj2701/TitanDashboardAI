// Updated dataLoader.ts for PostGIS cv_points table
import { aggregateCvRoads, listCvRoads, queryCv } from '../api/cvClient';
import { getSessionId } from '../api/session';

export interface ProcessedVehicleData {
  id: string;
  type: string;
  longitude: number;
  latitude: number;
  timestamp: string;
  speed: number;
  bearing: number;
  acceleration: { x: number; y: number };
  speedLimit: number;
  speedLimitKnown?: boolean;
  roadName: string;
  eventType?: string;
  eventDate?: string;
  decelerationG?: number;
  speedOverLimit?: number;
  speedBefore?: number;
  speedAfter?: number;
  speedDelta?: number;
  eventDuration?: number | null;
  severity?: string;
  crashSeverity?: string;
  accident_date?: string;
  accident_time?: string;
  hp_acc_image_no?: string;
  primary_id?: string;
  vehicle_id?: string;
  county?: string;
  func_class?: string;
  original_road?: string;
  road_segment_id?: string;
}

export interface CvRoadAggregateProperties {
  road_segment_id?: string;
  way_id?: number | string;
  road_name?: string;
  label?: string;
  ref?: string;
  name?: string;
  highway?: string;
  avg_speed_mph?: number;
  speed_limit_p50_mph?: number;
  speed_limit_p90_mph?: number;
  speed_limit_mph?: number;
  min_speed_mph?: number;
  max_speed_mph?: number;
  start_ts?: string;
  end_ts?: string;
  point_count?: number;
  crash_count?: number;
  hard_brake_count?: number;
  area_analysis?: boolean;
  avg_match_dist_m?: number;
  p90_match_dist_m?: number;
  unique_vehicles_total?: number;
  avg_unique_vehicles_per_hour?: number;
  hourly_unique_vehicles_json?: Record<string, number>;
}

export type CvRoadAggregateGeoJSON = GeoJSON.FeatureCollection<
  GeoJSON.LineString | GeoJSON.MultiLineString,
  CvRoadAggregateProperties
>;

interface CVQueryParams {
  dataset_id?: string;
  road_name?: string;  // e.g., "I-70", "IS 70", "I-44"
  limit?: number;
  min_speed?: number;
  max_speed?: number;
  source_table?: string;
}

interface CVRoadAggregateParams {
  dataset_id?: string;
  source_table?: string;
  min_points?: number;
  road_name?: string;
  road_names?: string[];
  road_segment_id?: string;
  limit?: number;
}

/**
 * Load CV data from PostGIS cv_points table with optional filters
 */
export const loadVehicleData = async (
  filters?: CVQueryParams
): Promise<ProcessedVehicleData[]> => {
  try {
    getSessionId();

    const result = await queryCv({
      dataset_id: filters?.dataset_id,
      road_name: filters?.road_name,
      limit: filters?.limit || 25000,
      min_speed: filters?.min_speed,
      max_speed: filters?.max_speed,
      source_table: filters?.source_table || 'cv_points',
    });
    
    if (result.status !== 'success') {
      throw new Error('Query failed');
    }

    console.log(`Loaded ${result.count} CV records from PostGIS`, result.filters_applied);
    const rows = Array.isArray(result.data) ? result.data : [];

    // Process and normalize the data
    const processed = rows.map((row: any) => {
      // Ensure all numeric values are valid
      const toNumber = (value: any, fallback: number = 0) => {
        const num = typeof value === 'number' ? value : parseFloat(value);
        return Number.isFinite(num) ? num : fallback;
      };

      return {
        id: row.id || `vehicle-${Math.random()}`,
        type: row.type || 'Vehicle',
        longitude: toNumber(row.longitude),
        latitude: toNumber(row.latitude),
        timestamp: row.timestamp || '',
        speed: toNumber(row.speed),
        bearing: toNumber(row.bearing),
        acceleration: {
          x: toNumber(
            row.acceleration?.x ??
              row.acc_x ??
              row.accX ??
              row.AccX
          ),
          y: toNumber(
            row.acceleration?.y ??
              row.acc_y ??
              row.accY ??
              row.AccY
          ),
        },
        speedLimit: toNumber(row.speedLimit, 1), // Fallback to 1 to avoid division by zero
        roadName: row.roadName || row.road_name || 'Unknown road',
        eventType: row.eventType || row.type,
        eventDate: row.eventDate || row.timestamp,
        vehicle_id: row.vehicle_id,
        county: row.county,
        func_class: row.func_class,
        original_road: row.original_road,
        road_segment_id: row.road_segment_id,
        // Optional fields
        decelerationG: row.decelerationG,
        speedOverLimit: row.speedOverLimit,
        speedBefore: row.speedBefore,
        speedAfter: row.speedAfter,
        speedDelta: row.speedDelta,
        eventDuration: row.eventDuration,
        severity: row.severity,
        crashSeverity: row.crashSeverity || row.severity,
        accident_date: row.accident_date,
        accident_time: row.accident_time,
        hp_acc_image_no: row.hp_acc_image_no || row.primary_id,
        primary_id: row.primary_id,
      } as ProcessedVehicleData;
    });

    const nonzero = processed.find(
      (row: ProcessedVehicleData) => Math.abs(row.acceleration?.x ?? 0) > 0.0001 || Math.abs(row.acceleration?.y ?? 0) > 0.0001
    );
    const sample = nonzero ?? processed[0];
    if (sample) {
      console.debug('[CV] loaded acceleration sample', {
        total: processed.length,
        sampleId: sample.id,
        accX: sample.acceleration?.x ?? null,
        accY: sample.acceleration?.y ?? null,
        speed: sample.speed,
      });
    }

    return processed;
  } catch (error) {
    console.error('Error loading vehicle data from PostGIS:', error);
    throw error;
  }
};

/**
 * Load aggregated CV data by road segment (avg/min/max speeds, date range).
 */
export const loadCvRoadAggregates = async (
  filters?: CVRoadAggregateParams
): Promise<CvRoadAggregateGeoJSON> => {
  getSessionId();
  const result = await aggregateCvRoads({
    dataset_id: filters?.dataset_id,
    source_table: filters?.source_table,
    min_points: filters?.min_points,
    road_name: filters?.road_name,
    road_names: filters?.road_names,
    road_segment_id: filters?.road_segment_id,
    limit: filters?.limit,
  });
  if (result.status !== 'success' || !result.geojson) {
    throw new Error('Aggregate query failed');
  }

  return result.geojson as CvRoadAggregateGeoJSON;
};

/**
 * Get list of available roads from the database
 */
export const getAvailableRoads = async (): Promise<string[]> => {
  try {
    getSessionId();
    const result = await listCvRoads();
    return result.roads || [];
  } catch (error) {
    console.error('Error fetching available roads:', error);
    return [];
  }
};

/**
 * Get unique vehicles from the dataset
 */
export const getUniqueVehicles = (data: ProcessedVehicleData[]): string[] => {
  return [...new Set(data.map((d) => d.id))];
};

/**
 * Filter data by vehicle ID
 */
export const filterByVehicle = (
  data: ProcessedVehicleData[],
  vehicleId: string
): ProcessedVehicleData[] => {
  return data.filter((d) => d.id === vehicleId);
};

/**
 * Filter data by time range
 */
export const filterByTimeRange = (
  data: ProcessedVehicleData[],
  startTime: string,
  endTime: string
): ProcessedVehicleData[] => {
  return data.filter((d) => {
    const timestamp = new Date(d.timestamp);
    return timestamp >= new Date(startTime) && timestamp <= new Date(endTime);
  });
};

/**
 * Get vehicles exceeding speed limit
 */
export const getSpeedingVehicles = (
  data: ProcessedVehicleData[]
): ProcessedVehicleData[] => {
  return data.filter((d) => d.speed > d.speedLimit);
};

/**
 * Get statistics for the dataset
 */
export const getDataStatistics = (data: ProcessedVehicleData[]) => {
  if (data.length === 0) {
    return {
      totalRecords: 0,
      uniqueVehicles: 0,
      averageSpeed: '0',
      maxSpeed: 0,
      minSpeed: 0,
      speedingViolations: 0,
      speedingPercentage: '0',
    };
  }

  const speeds = data.map((d) => d.speed);
  const avgSpeed = speeds.reduce((a, b) => a + b, 0) / speeds.length;
  const maxSpeed = Math.max(...speeds);
  const minSpeed = Math.min(...speeds);
  const uniqueVehicles = getUniqueVehicles(data).length;
  const speedingCount = getSpeedingVehicles(data).length;

  return {
    totalRecords: data.length,
    uniqueVehicles,
    averageSpeed: avgSpeed.toFixed(2),
    maxSpeed,
    minSpeed,
    speedingViolations: speedingCount,
    speedingPercentage: ((speedingCount / data.length) * 100).toFixed(2),
  };
};

export interface WorkzoneLine {
  id: string;
  coordinates: [number, number][];
  roadName: string;
  roadSegmentId?: string;
  datasetId?: string;
  status?: string;
  description?: string;
  startDate?: string;
  endDate?: string;
  exclusive?: boolean;
}

export type VehicleFeatureProperties = {
  id: string;
  type: string;
  eventType?: string;
  speed: number;
  speedLimit: number;
  speedLimitKnown?: boolean;
  speedRatio: number;
  speedStatus: 'above' | 'below' | 'at';
  bearing: number;
  accX: number;
  accY: number;
  roadName: string;
  timestamp: string;
  eventDate?: string;
  decelerationG: number | null;
  speedOverLimit: number | null;
  speedBefore: number;
  speedAfter: number;
  speedDelta: number;
  eventDuration: number | null;
  longitude: number;
  latitude: number;
  severity?: string;
  crashSeverity?: string;
  accident_date?: string;
  accident_time?: string;
  hp_acc_image_no?: string;
  primary_id?: string;
  road_segment_id?: string;
  matched_partner_ids?: string[];
  match_count?: number;
};

export type AreaSummary = {
  points: number;
  vehicles: number;
  avg_speed?: number | null;
  avg_unique_vehicles_per_hour?: number | null;
  hourly_unique_vehicles?: Record<string, number> | null;
  min_speed?: number | null;
  max_speed?: number | null;
  speeding_points: number;
  under_points: number;
  limit_points: number;
  speeding_pct: number;
  under_pct: number;
  time_start?: string | null;
  time_end?: string | null;
  crashes: number;
  workzones: number;
  hard_brakes?: number;
  area_km2?: number;
  road_segments?: number;
  crash_data_available?: boolean;
  workzone_data_available?: boolean;
  vehicles_available?: boolean;
  use_hard_brake_secondary?: boolean;
  secondary_stat_label?: string;
  secondary_stat_value?: number;
  fast_aggregate_mode?: boolean;
  approximate?: boolean;
  crash_by_road?: Array<{ road_name?: string; count?: number }>;
  hard_brake_by_road?: Array<{ road_name?: string; count?: number }>;
};

export type RoadAggregateFilter = {
  road_name?: string;
  road_segment_id?: string;
  road_segment_ids?: string[];
  road_names?: string[];
  min_points?: number;
  limit?: number;
};

export type LayerBehaviorMode = 'road-network' | 'focus-selection' | 'mixed';

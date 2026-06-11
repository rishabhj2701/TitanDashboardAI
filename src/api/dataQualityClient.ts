import { apiFetchApp } from './http';

export interface FieldQuality {
  non_null: number;
  null_count: number;
  completeness: number;
}

export interface DataQualityResponse {
  status?: string;
  total_rows: number;
  fields: Record<string, FieldQuality>;
  overall_completeness: number;
}

export const getDataQuality = async (): Promise<DataQualityResponse> => {
  const response = await apiFetchApp('/api/cv/data-quality');
  return response.json();
};

export interface SummaryStatsResponse {
  status?: string;
  total: number;
  cvPoints: number;
  crashes: number;
  hardBraking: number;
  avgSpeed: number;
  maxSpeed: number;
}

export const getSummaryStats = async (): Promise<SummaryStatsResponse> => {
  const response = await apiFetchApp('/api/cv/summary-stats');
  return response.json();
};

export interface SpeedComplianceResponse {
  status?: string;
  total: number;
  within_limit: number;
  over_limit: number;
  no_limit_data: number;
  within_pct: number;
  over_pct: number;
}

export const getSpeedCompliance = async (): Promise<SpeedComplianceResponse> => {
  const response = await apiFetchApp('/api/cv/speed-compliance');
  return response.json();
};

export interface TopSpeedingRoad {
  road_name: string;
  avg_speed_mph: number;
  speed_limit_mph: number;
  speed_over_limit: number;
  point_count: number;
}

export interface TopSpeedingRoadsResponse {
  status?: string;
  roads: TopSpeedingRoad[];
}

export const getTopSpeedingRoads = async (limit = 10): Promise<TopSpeedingRoadsResponse> => {
  const response = await apiFetchApp(`/api/cv/top-speeding-roads?limit=${limit}`);
  return response.json();
};

export interface HourlyTrendItem {
  hour: number;
  avg_speed: number;
  point_count: number;
}

export interface HourlyTrendResponse {
  status?: string;
  hours: HourlyTrendItem[];
}

export const getHourlyTrend = async (): Promise<HourlyTrendResponse> => {
  const response = await apiFetchApp('/api/cv/hourly-trend');
  return response.json();
};

export interface CountyItem {
  county: string;
  point_count: number;
  avg_speed: number;
}

export interface CountyBreakdownResponse {
  status?: string;
  counties: CountyItem[];
}

export const getCountyBreakdown = async (): Promise<CountyBreakdownResponse> => {
  const response = await apiFetchApp('/api/cv/county-breakdown');
  return response.json();
};

export interface FuncClassItem {
  func_class: string;
  point_count: number;
  avg_speed: number;
  avg_speed_limit: number;
}

export interface FuncClassResponse {
  status?: string;
  classes: FuncClassItem[];
}

export const getFuncClassStats = async (): Promise<FuncClassResponse> => {
  const response = await apiFetchApp('/api/cv/func-class-stats');
  return response.json();
};

export interface SpeedBucket {
  bucket_min: number;
  bucket_max: number;
  point_count: number;
}

export interface SpeedDistributionResponse {
  status?: string;
  buckets: SpeedBucket[];
  bucket_size: number;
}

export const getSpeedDistribution = async (bucketSize = 10): Promise<SpeedDistributionResponse> => {
  const response = await apiFetchApp(`/api/cv/speed-distribution?bucket_size=${bucketSize}`);
  return response.json();
};

export interface RoadVolumeItem {
  road_name: string;
  point_count: number;
  avg_speed_mph: number;
}

export interface TopRoadsVolumeResponse {
  status?: string;
  roads: RoadVolumeItem[];
}

export const getTopRoadsVolume = async (limit = 15): Promise<TopRoadsVolumeResponse> => {
  const response = await apiFetchApp(`/api/cv/top-roads-volume?limit=${limit}`);
  return response.json();
};

export interface DayOfWeekItem {
  dow: number;
  avg_speed: number;
  point_count: number;
}

export interface DayOfWeekTrendResponse {
  status?: string;
  days: DayOfWeekItem[];
}

export const getDayOfWeekTrend = async (): Promise<DayOfWeekTrendResponse> => {
  const response = await apiFetchApp('/api/cv/day-of-week-trend');
  return response.json();
};

export interface SpeedVsLimitRoad {
  road_name: string;
  avg_speed_mph: number;
  speed_limit_mph: number;
  point_count: number;
}

export interface SpeedVsLimitResponse {
  status?: string;
  roads: SpeedVsLimitRoad[];
}

export const getSpeedVsLimit = async (limit = 20): Promise<SpeedVsLimitResponse> => {
  const response = await apiFetchApp(`/api/cv/speed-vs-limit?limit=${limit}`);
  return response.json();
};

export interface HourlyVehicleCountItem {
  hour: number;
  total_vehicles: number;
}

export interface HourlyVehicleCountsResponse {
  status?: string;
  hours: HourlyVehicleCountItem[];
  total_unique_vehicles: number;
}

export const getHourlyVehicleCounts = async (): Promise<HourlyVehicleCountsResponse> => {
  const response = await apiFetchApp('/api/cv/hourly-vehicle-counts');
  return response.json();
};
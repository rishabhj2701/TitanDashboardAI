import type { GeneratedChartPayload } from './charts';

export type WidgetType =
  | 'chart'
  | 'stat-summary'
  | 'data-quality'
  | 'road-stats'
  | 'speed-compliance'
  | 'top-speeding-roads'
  | 'hourly-trend'
  | 'county-breakdown'
  | 'func-class-stats'
  | 'speed-distribution'
  | 'top-roads-volume'
  | 'day-of-week-trend'
  | 'speed-vs-limit'
  | 'hourly-vehicle-count';

export interface WidgetLayout {
  x: number;
  y: number;
  w: number;
  h: number;
  minW?: number;
  minH?: number;
}

interface BaseWidget {
  id: string;
  type: WidgetType;
  title: string;
  addedAt: number;
  layout?: WidgetLayout;
  locked?: boolean;
  collapsed?: boolean;
  colorTag?: string;
  note?: string;
}

export interface ChartWidget extends BaseWidget {
  type: 'chart';
  chartPayload: GeneratedChartPayload;
}

export interface StatSummaryWidget extends BaseWidget {
  type: 'stat-summary';
}

export interface DataQualityWidget extends BaseWidget {
  type: 'data-quality';
}

export interface RoadStatsData {
  road_name: string;
  road_segment_id: string;
  avg_speed_mph: number | null;
  speed_limit_mph: number | null;
  p50_speed_mph: number | null;
  p90_speed_mph: number | null;
  crash_count?: number | null;
  hard_brake_count?: number | null;
  area_analysis?: boolean;
  point_count: number | null;
  unique_vehicles_total?: number | null;
  avg_unique_vehicles_per_hour?: number | null;
  peak_hour?: string | null;
  hourly_unique_vehicles_json?: Record<string, number> | null;
  pinnedAt: number;
}

export interface RoadStatsWidget extends BaseWidget {
  type: 'road-stats';
  roadData: RoadStatsData;
}

export interface SpeedComplianceWidget extends BaseWidget { type: 'speed-compliance'; }
export interface TopSpeedingRoadsWidget extends BaseWidget { type: 'top-speeding-roads'; }
export interface HourlyTrendWidget extends BaseWidget { type: 'hourly-trend'; }
export interface CountyBreakdownWidget extends BaseWidget { type: 'county-breakdown'; }
export interface FuncClassStatsWidget extends BaseWidget { type: 'func-class-stats'; }
export interface SpeedDistributionWidget extends BaseWidget { type: 'speed-distribution'; }
export interface TopRoadsVolumeWidget extends BaseWidget { type: 'top-roads-volume'; }
export interface DayOfWeekTrendWidget extends BaseWidget { type: 'day-of-week-trend'; }
export interface SpeedVsLimitWidget extends BaseWidget { type: 'speed-vs-limit'; }
export interface HourlyVehicleCountWidget extends BaseWidget { type: 'hourly-vehicle-count'; }

export type DashboardWidget =
  | ChartWidget
  | StatSummaryWidget
  | DataQualityWidget
  | RoadStatsWidget
  | SpeedComplianceWidget
  | TopSpeedingRoadsWidget
  | HourlyTrendWidget
  | CountyBreakdownWidget
  | FuncClassStatsWidget
  | SpeedDistributionWidget
  | TopRoadsVolumeWidget
  | DayOfWeekTrendWidget
  | SpeedVsLimitWidget
  | HourlyVehicleCountWidget;

export const ANALYTICS_WIDGET_TYPES: WidgetType[] = [
  'speed-compliance',
  'top-speeding-roads',
  'hourly-trend',
  'county-breakdown',
  'func-class-stats',
  'speed-distribution',
  'top-roads-volume',
  'day-of-week-trend',
  'speed-vs-limit',
  'hourly-vehicle-count',
];

export const ANALYTICS_WIDGET_LABELS: Record<string, string> = {
  'speed-compliance': 'Speed Compliance',
  'top-speeding-roads': 'Top Speeding Roads',
  'hourly-trend': 'Hourly Speed Trend',
  'county-breakdown': 'County Breakdown',
  'func-class-stats': 'Functional Class Stats',
  'speed-distribution': 'Speed Distribution',
  'top-roads-volume': 'Top Roads by Volume',
  'day-of-week-trend': 'Day of Week Trend',
  'speed-vs-limit': 'Speed vs Limit',
  'hourly-vehicle-count': 'Hourly Vehicle Count',
};

export const DEFAULT_WIDGET_LAYOUTS: Record<WidgetType, WidgetLayout> = {
  'chart':              { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'stat-summary':       { x: 0, y: 0, w: 4, h: 3, minW: 3, minH: 2 },
  'data-quality':       { x: 0, y: 0, w: 6, h: 5, minW: 4, minH: 3 },
  'road-stats':         { x: 0, y: 0, w: 4, h: 3, minW: 3, minH: 2 },
  'speed-compliance':   { x: 0, y: 0, w: 4, h: 4, minW: 3, minH: 3 },
  'top-speeding-roads': { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'hourly-trend':       { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'county-breakdown':   { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'func-class-stats':   { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'speed-distribution': { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'top-roads-volume':   { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'day-of-week-trend':  { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'speed-vs-limit':     { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
  'hourly-vehicle-count': { x: 0, y: 0, w: 6, h: 4, minW: 4, minH: 3 },
};

export interface Dashboard {
  id: string;
  name: string;
  widgets: DashboardWidget[];
  createdAt: number;
}

export interface DashboardStore {
  dashboards: Dashboard[];
  activeDashboardId: string;
}

/* -------- Dashboard Templates -------- */
export interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  widgetTypes: WidgetType[];
}

export const DASHBOARD_TEMPLATES: DashboardTemplate[] = [
  {
    id: 'safety',
    name: 'Safety Dashboard',
    description: 'Speed compliance, top speeding roads, speed vs limit analysis',
    widgetTypes: ['stat-summary', 'speed-compliance', 'top-speeding-roads', 'speed-vs-limit'],
  },
  {
    id: 'traffic-volume',
    name: 'Traffic Volume',
    description: 'Road volume, hourly & daily trends, county breakdown',
    widgetTypes: ['stat-summary', 'top-roads-volume', 'hourly-trend', 'hourly-vehicle-count', 'day-of-week-trend', 'county-breakdown'],
  },
  {
    id: 'speed-analysis',
    name: 'Speed Analysis',
    description: 'Speed distribution, functional class, hourly trends, compliance',
    widgetTypes: ['speed-distribution', 'speed-compliance', 'hourly-trend', 'func-class-stats', 'speed-vs-limit'],
  },
  {
    id: 'complete',
    name: 'Complete Overview',
    description: 'All analytics widgets in one dashboard',
    widgetTypes: ['stat-summary', 'data-quality', ...ANALYTICS_WIDGET_TYPES as unknown as WidgetType[]],
  },
];

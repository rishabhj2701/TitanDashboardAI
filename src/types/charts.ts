export type Orientation = 'horizontal' | 'vertical';

export type ChartEditParamType = 'number' | 'integer' | 'slider' | 'percent' | 'select' | 'text';

export interface ChartEditParam {
  key: string;
  label: string;
  type: ChartEditParamType;
  value?: number | string | null;
  min?: number;
  max?: number;
  step?: number;
  options?: Array<string | number>;
  keywords?: string[];
}

export interface ChartFilterParam {
  key: string;
  label: string;
  column: string;
  type: string;
  operators?: string[];
  min?: number;
  max?: number;
  options?: Array<string | number>;
  keywords?: string[];
  description?: string;
}

export interface ChartEditMeta {
  pipelineId?: string;
  chartRole?: string;
  description?: string;
  editableParams?: ChartEditParam[];
  filterableParams?: ChartFilterParam[];
  currentFilters?: Array<{ column: string; operator: string; value: any }>;
  originalFilters?: Array<{ column: string; operator: string; value: any }>;
  columnsUsed?: string[];
}

export interface ChartCategoryTarget {
  road_segment_id?: string | null;
  road_name?: string | null;
}

interface BaseChartPayload {
  meta?: ChartEditMeta;
  categoryTargets?: ChartCategoryTarget[];
}

export interface SeriesPayload {
  label: string;
  values: Array<number | string | null>;
  axis?: 'left' | 'right';
}

export interface BarChartPayload extends BaseChartPayload {
  type: 'bar';
  title?: string;
  xLabel: string;
  yLabel: string;
  xValues: any[];
  series: SeriesPayload[];
  orientation?: Orientation;
  categoryValues?: any[];
}

export interface DualAxisChartPayload extends BaseChartPayload {
  type: 'dual_axis';
  title?: string;
  xLabel: string;
  leftLabel: string;
  rightLabel: string;
  xValues: any[];
  series: SeriesPayload[];
}

export interface ImageChartPayload extends BaseChartPayload {
  type: 'image';
  title?: string;
  imageUrl: string;
}

export interface PieChartPayload extends BaseChartPayload {
  type: 'pie';
  title?: string;
  xLabel?: string;
  yLabel?: string;
  xValues: any[];
  series: SeriesPayload[];
}

export interface DoughnutChartPayload extends BaseChartPayload {
  type: 'doughnut';
  title?: string;
  xLabel?: string;
  yLabel?: string;
  xValues: any[];
  series: SeriesPayload[];
}

export type GeneratedChartPayload = BarChartPayload | DualAxisChartPayload | ImageChartPayload | PieChartPayload | DoughnutChartPayload;

export interface ChartBarClickContext {
  payload: GeneratedChartPayload;
  categoryIndex: number;
  datasetIndex: number;
  label: string;
  value: number | null;
  target?: ChartCategoryTarget;
}

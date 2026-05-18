import type { Expression } from 'mapbox-gl';

export type RoadColorMode = 'speed-compliance' | 'vehicle-count';
export type RoadTileMode = 'speed' | 'vehicles';

export const ROAD_NO_DATA_COLOR = '#9e9e9e';

const SPEED_COMPLIANCE_COLOR_EXPR: Expression = [
  'let',
  'avg',
  ['coalesce', ['to-number', ['get', 'avg_speed_mph']], ['to-number', ['get', 'avg_speed']], 0],
  'limit',
  ['coalesce', ['to-number', ['get', 'speed_limit_mph']], ['to-number', ['get', 'speed_limit_mode']], -1],
  [
    'case',
    ['<=', ['var', 'limit'], 0], ROAD_NO_DATA_COLOR,
    [
      'let',
      'delta',
      ['-', ['var', 'avg'], ['var', 'limit']],
      [
        'case',
        ['>=', ['var', 'delta'], 10], '#8b0000',
        ['<=', ['var', 'delta'], -10], '#e53935',
        '#2e7d32',
      ],
    ],
  ],
];

export const VEHICLE_COUNT_STOPS: ReadonlyArray<[number, string]> = [
  [0, '#b71c1c'],
  [1, '#d32f2f'],
  [2, '#e65100'],
  [3, '#ef6c00'],
  [5, '#f9a825'],
  [8, '#fdd835'],
  [15, '#c0ca33'],
  [25, '#7cb342'],
  [50, '#388e3c'],
  [100, '#1b5e20'],
];

const VEHICLE_COUNT_COLOR_EXPR: Expression = [
  'case',
  [
    'any',
    ['!', ['has', 'unique_vehicles_total']],
    ['==', ['get', 'unique_vehicles_total'], null],
  ],
  ROAD_NO_DATA_COLOR,
  [
    'interpolate',
    ['linear'],
    ['to-number', ['coalesce', ['get', 'unique_vehicles_total'], 0], 0],
    ...VEHICLE_COUNT_STOPS.flatMap(([stop, color]) => [stop, color]),
  ],
];

export const getRoadLineColorExpression = (mode: RoadColorMode): Expression => {
  return mode === 'vehicle-count' ? VEHICLE_COUNT_COLOR_EXPR : SPEED_COMPLIANCE_COLOR_EXPR;
};

export const roadColorModeToTileMode = (mode: RoadColorMode): RoadTileMode =>
  mode === 'vehicle-count' ? 'vehicles' : 'speed';

export const getRoadVehicleCountColor = (value: unknown): string => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return ROAD_NO_DATA_COLOR;
  let chosen = VEHICLE_COUNT_STOPS[0][1];
  for (const [stop, color] of VEHICLE_COUNT_STOPS) {
    if (numeric >= stop) chosen = color;
  }
  return chosen;
};

type LegendSegment = { color: string; flex: number };
type LegendItem = { color: string; label: string };
type LegendConfig = {
  gradient: LegendSegment[];
  labels: string[];
  items: LegendItem[];
};

export const ROAD_LEGEND_BY_MODE: Record<RoadColorMode, LegendConfig> = {
  'speed-compliance': {
    gradient: [
      { color: '#e53935', flex: 1 },
      { color: '#2e7d32', flex: 2 },
      { color: '#8b0000', flex: 1 },
    ],
    labels: ['-10+ mph', 'Within limit', '+10+ mph'],
    items: [
      { color: '#2e7d32', label: 'Within ±10 mph of limit' },
      { color: '#e53935', label: 'More than 10 mph below limit' },
      { color: '#8b0000', label: 'More than 10 mph above limit' },
      { color: ROAD_NO_DATA_COLOR, label: 'No speed-limit data' },
    ],
  },
  'vehicle-count': {
    gradient: [
      { color: '#b71c1c', flex: 1 },
      { color: '#e65100', flex: 1 },
      { color: '#f9a825', flex: 1 },
      { color: '#fdd835', flex: 1 },
      { color: '#c0ca33', flex: 1 },
      { color: '#7cb342', flex: 1 },
      { color: '#388e3c', flex: 1 },
      { color: '#1b5e20', flex: 1 },
    ],
    labels: ['1-2', '5-15', '25-50', '100+'],
    items: [
      { color: ROAD_NO_DATA_COLOR, label: 'No vehicle data' },
      { color: '#d32f2f', label: 'Low vehicle density' },
      { color: '#f9a825', label: 'Moderate vehicle density' },
      { color: '#1b5e20', label: 'High vehicle density' },
    ],
  },
};

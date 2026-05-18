import type { ProcessedVehicleData } from '../../services/dataLoader';
import type { WorkzoneLinePayload } from './types';

type GenericObject = Record<string, unknown>;

export const asObject = (value: unknown): GenericObject => (
  value && typeof value === 'object' ? (value as GenericObject) : {}
);

export const toNumber = (value: unknown, fallback: number = 0): number => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const toFiniteOrNaN = (value: unknown): number => {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
};

export const toStringOrEmpty = (value: unknown): string => (value == null ? '' : String(value));

export const toStringOptional = (value: unknown): string | undefined => {
  if (value == null) return undefined;
  const str = String(value);
  return str.trim() ? str : undefined;
};

const canonicalPointType = (value: unknown): string => {
  const raw = toStringOrEmpty(value).trim();
  if (!raw) return 'Vehicle';
  const lowered = raw.toLowerCase();
  const compact = lowered.replace(/[^a-z0-9]/g, '');

  if (compact.includes('hardbrake') || compact.includes('hardbraking')) {
    return 'HardBrake';
  }
  if (compact.includes('crash') || compact.includes('accident')) {
    return 'Crash';
  }
  if (compact.includes('workzone')) {
    return 'Workzone';
  }
  if (compact.includes('traffic') || compact.includes('vehicle')) {
    return 'Vehicle';
  }
  return raw;
};

const mapServerSelectionPoint = (rawPoint: unknown, idx: number): ProcessedVehicleData => {
  const point = asObject(rawPoint);
  const coords = Array.isArray(point.coordinates) ? point.coordinates : null;
  const latitude = coords ? Number(coords[0]) : Number(point.latitude);
  const longitude = coords ? Number(coords[1]) : Number(point.longitude);
  const acceleration = asObject(point.acceleration);
  const rawSpeedLimit =
    point.speedLimit ??
    point.SpeedLimitMPH ??
    point.speedLimitMPH ??
    point.speed_mph_limit ??
    point.speed_limit_mph ??
    point.speed_limit ??
    null;
  const speedLimitNum = rawSpeedLimit == null ? Number.NaN : Number(rawSpeedLimit);
  const speedLimitKnown = Number.isFinite(speedLimitNum) && speedLimitNum > 0;
  const canonicalType = canonicalPointType(
    point.type ?? point.point_type ?? point.datasetType ?? point.entity_type
  );
  const mappedRoadName = toStringOrEmpty(
    point.roadName ?? point.road_name ?? point.road ?? point.label ?? point.ref
  );
  const mappedTimestamp = toStringOrEmpty(point.timestamp ?? point.ts ?? point.event_ts);
  const hardBrakeDecel = toFiniteOrNaN(
    point.decelerationG ?? point.acc_x ?? point.accX ?? point.accel_x ?? point.AccX
  );
  const mappedSpeed = toFiniteOrNaN(point.speed ?? point.speed_mph ?? point.Speed);

  return {
    id: String(point.id ?? `selection-${idx}`),
    type: canonicalType || 'Vehicle',
    longitude,
    latitude,
    timestamp: mappedTimestamp,
    speed: Number.isFinite(mappedSpeed) ? mappedSpeed : 0,
    bearing: toNumber(point.bearing),
    acceleration: {
      x: toFiniteOrNaN(acceleration.x ?? point.acc_x ?? point.accX ?? point.accel_x ?? point.AccX),
      y: toFiniteOrNaN(acceleration.y ?? point.acc_y ?? point.accY ?? point.accel_y ?? point.AccY),
    },
    speedLimit: speedLimitKnown ? speedLimitNum : 0,
    speedLimitKnown,
    roadName: mappedRoadName,
    eventType: toStringOptional(point.eventType ?? canonicalType),
    eventDate: toStringOptional(point.eventDate ?? mappedTimestamp),
    decelerationG: hardBrakeDecel,
    speedOverLimit: toFiniteOrNaN(point.speedOverLimit),
    speedBefore: toFiniteOrNaN(point.speedBefore),
    speedAfter: toFiniteOrNaN(point.speedAfter),
    speedDelta: toFiniteOrNaN(point.speedDelta),
    eventDuration: Number.isFinite(Number(point.eventDuration)) ? Number(point.eventDuration) : null,
    severity: toStringOptional(point.severity ?? point.crashSeverity),
    crashSeverity: toStringOptional(point.crashSeverity ?? point.severity),
    accident_date: toStringOptional(point.accident_date),
    accident_time: toStringOptional(point.accident_time),
    hp_acc_image_no: toStringOptional(point.hp_acc_image_no ?? point.primary_id),
    primary_id: toStringOptional(point.primary_id),
    road_segment_id: toStringOptional(point.road_segment_id),
  };
};

export const mapServerSelectionPoints = (rawPoints: unknown[]): ProcessedVehicleData[] => (
  rawPoints.map((point, idx) => mapServerSelectionPoint(point, idx))
);

const toCoordinatePairs = (value: unknown): [number, number][] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((pair) => {
      if (!Array.isArray(pair) || pair.length < 2) return null;
      return [Number(pair[0]), Number(pair[1])] as [number, number];
    })
    .filter((pair): pair is [number, number] => pair !== null);
};

export const mapServerWorkzoneLines = (rawLines: unknown[]): WorkzoneLinePayload[] => (
  rawLines.map((rawLine, idx) => {
    const line = asObject(rawLine);
    return {
      id: String(line.id ?? `workzone-line-${idx}`),
      coordinates: toCoordinatePairs(line.coordinates),
      roadName: toStringOrEmpty(line.roadName) || 'Unknown',
      roadSegmentId: toStringOptional(line.roadSegmentId ?? line.road_segment_id),
      datasetId: toStringOptional(line.datasetId ?? line.dataset_id),
      status: toStringOptional(line.status),
      description: toStringOptional(line.description),
      startDate: toStringOptional(line.startDate),
      endDate: toStringOptional(line.endDate),
      exclusive: line.exclusive !== false,
    };
  })
);

export const detectDatasetType = (data: unknown[], columns: string[], fileName: string): string => {
  const lowerFileName = fileName.toLowerCase();
  const hasWorkzoneRows = data.some((row) => {
    const item = asObject(row);
    return 'geometry' in item && 'properties' in item;
  });

  if (hasWorkzoneRows || (columns.includes('geometry') && columns.includes('properties'))) {
    return 'workzone';
  }

  const crashColumns = ['crash_id', 'crashid', 'accident_id', 'severity', 'injury', 'fatality', 'collision'];
  const hasCrashColumns = crashColumns.some(col =>
    columns.some(c => c.toLowerCase().includes(col))
  );
  if (hasCrashColumns || lowerFileName.includes('crash') || lowerFileName.includes('accident')) {
    return 'crash';
  }

  const trafficColumns = ['vehicle', 'speed', 'latitude', 'longitude', 'bearing', 'acceleration'];
  const hasTrafficColumns = trafficColumns.filter(col =>
    columns.some(c => c.toLowerCase().includes(col))
  ).length >= 3;
  if (hasTrafficColumns || lowerFileName.includes('traffic') || lowerFileName.includes('vehicle')) {
    return 'traffic';
  }

  return 'primary';
};

export const getCodeMappingReminder = (entityType: string | undefined, codebookInfo: unknown): string | null => {
  const entity = (entityType || '').toLowerCase();
  if (entity !== 'crash' && entity !== 'workzone') return null;

  const codebook = asObject(codebookInfo);
  const missingRaw = codebook.missing;
  const missing = Array.isArray(missingRaw)
    ? missingRaw.filter((v): v is string => typeof v === 'string' && v.trim().length > 0)
    : [];
  if (!missing.length) return null;

  const sample = missing.slice(0, 4).join(', ');
  const suffix = missing.length > 4 ? ', ...' : '';
  return `Coded columns detected (${sample}${suffix}). Upload attributes/codebook file to decode them.`;
};

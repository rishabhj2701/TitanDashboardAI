import type { ProcessedVehicleData } from '../../services/dataLoader';
import type { VehicleFeatureProperties, WorkzoneLine } from './types';

const SPEED_TOLERANCE = 0.5;

export function buildFeatureCollection(
  data: ProcessedVehicleData[]
): GeoJSON.FeatureCollection<GeoJSON.Point, VehicleFeatureProperties> {
  const features = data
    .map((vehicle): GeoJSON.Feature<GeoJSON.Point, VehicleFeatureProperties> | null => {
      const longitude = Number(vehicle.longitude);
      const latitude = Number(vehicle.latitude);
      const rawSpeed = Number(vehicle.speed);
      const safeSpeed = Number.isFinite(rawSpeed) ? rawSpeed : 0;

      const rawLimitCandidate =
        (vehicle as any).speedLimit ??
        (vehicle as any).SpeedLimitMPH ??
        (vehicle as any).speedLimitMPH ??
        (vehicle as any).speed_limit_mph ??
        (vehicle as any).speedlimit_mph ??
        (vehicle as any).speed_limit ??
        null;

      const rawOverCandidate =
        (vehicle as any).speedOverLimit ??
        (vehicle as any).speed_over_limit ??
        null;

      let limitNum = rawLimitCandidate != null ? Number(rawLimitCandidate) : NaN;
      const hasSpeedLimit = Number.isFinite(limitNum) && limitNum > 0;

      if (!Number.isFinite(limitNum) && rawOverCandidate != null) {
        const overNum = Number(rawOverCandidate);
        if (Number.isFinite(overNum)) limitNum = safeSpeed - overNum;
      }

      const safeLimit = hasSpeedLimit
        ? limitNum
        : (Number.isFinite(safeSpeed) && safeSpeed > 0 ? safeSpeed : 1);

      const safeSpeedOverLimit =
        rawOverCandidate == null
          ? null
          : (Number.isFinite(Number(rawOverCandidate)) ? Number(rawOverCandidate) : null);

      const speedRatio = safeSpeed / safeLimit;
      const delta = safeSpeed - safeLimit;
      const speedStatus =
        delta > SPEED_TOLERANCE ? 'above' : delta < -SPEED_TOLERANCE ? 'below' : 'at';
      const speedBeforeRaw = Number(vehicle.speedBefore ?? NaN);
      const speedBefore = Number.isFinite(speedBeforeRaw) ? speedBeforeRaw : safeSpeed;
      const speedAfterRaw = Number(vehicle.speedAfter ?? NaN);
      const speedAfter = Number.isFinite(speedAfterRaw) ? speedAfterRaw : safeSpeed;
      const speedDeltaRaw = Number(vehicle.speedDelta ?? NaN);
      const speedDelta = Number.isFinite(speedDeltaRaw)
        ? speedDeltaRaw
        : Math.abs(speedAfter - speedBefore);
      const eventDurationRaw = Number(vehicle.eventDuration ?? NaN);
      const eventDuration = Number.isFinite(eventDurationRaw) ? eventDurationRaw : null;

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: [longitude, latitude],
        },
        properties: {
          id: vehicle.id,
          type: vehicle.type,
          eventType: vehicle.eventType || vehicle.type,
          speed: safeSpeed,
          speedLimit: safeLimit,
          speedLimitKnown: hasSpeedLimit,
          speedRatio,
          speedStatus,
          bearing: Number(vehicle.bearing) || 0,
          accX: Number(vehicle.acceleration.x) || 0,
          accY: Number(vehicle.acceleration.y) || 0,
          roadName: (vehicle as any).roadName ?? (vehicle as any).road_name ?? null,
          timestamp: vehicle.timestamp,
          eventDate: vehicle.eventDate || vehicle.timestamp,
          decelerationG: typeof vehicle.decelerationG === 'number' ? vehicle.decelerationG : null,
          speedOverLimit: safeSpeedOverLimit,
          speedBefore,
          speedAfter,
          speedDelta,
          eventDuration,
          longitude,
          latitude,
          severity: vehicle.severity,
          crashSeverity: vehicle.crashSeverity || vehicle.severity,
          accident_date: vehicle.accident_date,
          accident_time: vehicle.accident_time,
          hp_acc_image_no: vehicle.hp_acc_image_no || vehicle.primary_id,
          primary_id: vehicle.primary_id,
          road_segment_id: (vehicle as any).road_segment_id ?? (vehicle as any).roadSegmentId ?? null,
        },
      };
    })
    .filter((feature): feature is GeoJSON.Feature<GeoJSON.Point, VehicleFeatureProperties> => Boolean(feature));

  return {
    type: 'FeatureCollection',
    features,
  };
}

export function buildWorkzoneLineCollection(lines: WorkzoneLine[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: lines.map((line) => ({
      type: 'Feature' as const,
      geometry: {
        type: 'LineString' as const,
        coordinates: line.coordinates,
      },
      properties: {
        id: line.id,
        roadName: line.roadName,
        roadSegmentId: line.roadSegmentId ?? '',
        datasetId: line.datasetId ?? '',
        status: line.status ?? 'unknown',
        description: line.description ?? '',
        startDate: line.startDate ?? '',
        endDate: line.endDate ?? '',
        exclusive: line.exclusive !== false,
      },
    })),
  };
}

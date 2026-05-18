import type { CvRoadAggregateProperties } from '../../services/dataLoader';
import { formatDateHuman, formatDateTimeHuman, formatTimeHuman } from '../../utils/dateTime';
import {
  computeAvgUniqueVehiclesPerHourFromHourlyJson,
  parseHourlyUniqueVehicles,
} from '../../utils/hourlyVehicles';
import { getRoadVehicleCountColor, type RoadColorMode } from './roadColorMode';

const formatCrashDate = (raw: unknown) => {
  const value = raw == null ? '' : String(raw).trim();
  if (!value) return 'N/A';
  if (value.length >= 10 && value[4] === '-' && value[7] === '-') {
    return value.slice(0, 10);
  }
  const match = value.match(/(\d{4}-\d{2}-\d{2})/);
  if (match) return match[1];
  return value;
};

const formatCrashTime = (raw: unknown) => {
  const value = raw == null ? '' : String(raw).trim();
  if (!value) return '';
  const match = value.match(/(\d{2}:\d{2}:\d{2})/);
  if (match) return match[1];
  const shortMatch = value.match(/(\d{2}:\d{2})/);
  if (shortMatch) return shortMatch[1];
  return value;
};

export const buildHardBrakePopupHtml = (props: Record<string, unknown>) => {
  const rawDecel =
    typeof props.decelerationG === 'number'
      ? props.decelerationG
      : Number(props.decelerationG ?? NaN);
  const fallbackDecel = Number(props.accX ?? props.acc_x ?? NaN);
  const decel = Number.isFinite(rawDecel) ? rawDecel : fallbackDecel;
  const decelLabel = Number.isFinite(decel) ? `${Math.abs(decel).toFixed(2)} g` : 'N/A';
  const severity = Number.isFinite(decel) ? Math.min(Math.abs(decel) * 3, 1) : 0.3;
  const severityColor = `rgba(255, 166, 0, ${0.4 + severity * 0.4})`;
  const speedNow = typeof props.speed === 'number' ? props.speed : Number(props.speed ?? NaN);
  const speedNowLabel = Number.isFinite(speedNow) ? `${speedNow.toFixed(0)} mph` : 'N/A';
  const matchCount = (props.match_count ?? props.matchCount) as number | null | undefined;
  return `
      <div style="
        padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: linear-gradient(135deg, rgba(20, 10, 40, 0.98), rgba(35, 10, 60, 0.98));
        color: #fff;
        border: 1px solid rgba(155, 93, 229, 0.5);
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 8px 32px rgba(155, 93, 229, 0.4);
        backdrop-filter: blur(10px);
        min-width: 220px;
      ">
        <div style="
          background: rgba(155, 93, 229, 0.2);
          padding: 10px 12px;
          border-bottom: 1px solid rgba(155, 93, 229, 0.3);
          display: flex;
          align-items: center;
          gap: 10px;
        ">
          <div style="font-size: 24px;">🛑</div>
          <div>
            <div style="font-size: 14px; font-weight: 700; color: #f9f871;">HARD BRAKE</div>
            <div style="font-size: 9px; color: rgba(255, 255, 255, 0.6);">${String(props.roadName || 'Unknown road')}</div>
          </div>
        </div>
        <div style="padding: 10px 12px;">
          <div style="font-size: 11px; line-height: 1.8; color: #e0e7ff;">
            <div style="display: flex; align-items: center; justify-content: space-between; background: ${severityColor}; padding: 6px 8px; border-radius: 6px; margin-bottom: 8px;">
              <span style="font-size: 11px;">Deceleration</span>
              <strong style="font-size: 13px;">${decelLabel}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 4px;">
              <span style="opacity: 0.7;">Current speed</span>
              <strong>${speedNowLabel}</strong>
            </div>
            ${matchCount != null ? `
            <div style="display:flex; justify-content:space-between; font-size:10px; margin-bottom:4px;">
              <span style="opacity:0.7;">Matched events</span>
              <strong>${matchCount}</strong>
            </div>` : ''}
            <div style="font-size: 9px; color: rgba(255, 255, 255, 0.55); margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(155, 93, 229, 0.2);">
              ${formatDateTimeHuman(props.eventDate ?? props.timestamp ?? props.ts, 'N/A')}
            </div>
          </div>
        </div>
      </div>
    `;
};

export const buildVehiclePopupHtml = (props: Record<string, unknown>) => {
  const speedVal = Number(props.speed) || 0;
  const speedLimitVal = props.speedLimitKnown === false
    ? NaN
    : (Number(props.speedLimit) || 0);
  const delta = speedVal - speedLimitVal;
  const speedBand = delta < -10 ? 'below' : delta > 10 ? 'above' : 'within';
  const statusColorMap = {
    within: '#2e7d32',
    below: '#e53935',
    above: '#8b0000',
  } as const;
  const statusColor = statusColorMap[speedBand];
  const statusBg =
    speedBand === 'within'
      ? 'rgba(46, 125, 50, 0.12)'
      : speedBand === 'below'
        ? 'rgba(229, 57, 53, 0.12)'
        : 'rgba(139, 0, 0, 0.12)';
  const statusLabel =
    speedBand === 'within'
      ? '✓ Within 10 mph'
      : speedBand === 'above'
        ? '⚠️ 10+ mph over'
        : '⚠️ 10+ mph under';
  const borderColor =
    speedBand === 'within'
      ? 'rgba(46, 125, 50, 0.35)'
      : speedBand === 'below'
        ? 'rgba(229, 57, 53, 0.35)'
        : 'rgba(139, 0, 0, 0.35)';
  const accXVal = Number(props.accX);
  const accYVal = Number(props.accY);
  const accXLabel = Number.isFinite(accXVal) ? `${accXVal.toFixed(2)} g` : 'N/A';
  const accYLabel = Number.isFinite(accYVal) ? `${accYVal.toFixed(2)} g` : 'N/A';

  return `
      <div style="
        padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: linear-gradient(135deg, rgba(10, 14, 39, 0.98), rgba(20, 25, 50, 0.98));
        color: #fff;
        border: 1px solid ${borderColor};
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6), 0 0 0 1px ${borderColor};
        backdrop-filter: blur(10px);
        min-width: 200px;
      ">
        <div style="
          background: ${statusBg};
          padding: 10px 12px;
          border-bottom: 1px solid rgba(100, 255, 218, 0.2);
          display: flex;
          align-items: center;
          gap: 8px;
        ">
          <div style="
            font-size: 24px;
            font-weight: 700;
            color: ${statusColor};
            text-shadow: 0 0 10px ${statusColor};
          ">${speedVal.toFixed(0)}</div>
          <div>
            <div style="font-size: 10px; color: rgba(255, 255, 255, 0.6); text-transform: uppercase; letter-spacing: 0.5px;">MPH</div>
            <div style="font-size: 9px; color: ${statusColor};">
              ${statusLabel}
            </div>
          </div>
        </div>
        <div style="padding: 10px 12px;">
          <div style="font-size: 11px; line-height: 1.8; color: #ccd6f6;">
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
              <span style="color: #64ffda;">📍</span>
              <span style="opacity: 0.9;">${String(props.roadName || '')}</span>
            </div>
            <div style="font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-top: 4px;">
              Limit: ${Number.isFinite(speedLimitVal) ? `${speedLimitVal.toFixed(0)} MPH` : 'N/A'}
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-top: 4px;">
              <span>AccX (g)</span>
              <strong style="color: #e6f1ff;">${accXLabel}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-top: 2px;">
              <span>AccY (g)</span>
              <strong style="color: #e6f1ff;">${accYLabel}</strong>
            </div>
            <div style="font-size: 9px; color: rgba(255, 255, 255, 0.5); margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(100, 255, 218, 0.1);">
              ${formatTimeHuman(props.timestamp, 'N/A')}
            </div>
          </div>
        </div>
      </div>
    `;
};

export const buildCrashPopupHtml = (
  props: Record<string, unknown>,
  coordinates: [number, number],
  includeAction: boolean,
  buttonId?: string
) => {
  const hasAccidentParts = Boolean(props.accident_date || props.accidentDate || props.accident_time || props.accidentTime);
  const timestampValue = props.timestamp ?? props.ts ?? null;
  const timestampDate = timestampValue ? new Date(String(timestampValue)) : null;
  const timestampValid = Boolean(timestampDate && !Number.isNaN(timestampDate.getTime()));
  const accidentDate = hasAccidentParts
    ? formatCrashDate(props.accident_date ?? props.accidentDate)
    : (timestampValid ? formatDateHuman(timestampDate, 'N/A') : formatCrashDate(props.timestamp));
  const accidentTime = hasAccidentParts
    ? formatCrashTime(props.accident_time ?? props.accidentTime)
    : (timestampValid ? formatTimeHuman(timestampDate, '') : formatCrashTime(props.timestamp));
  const combinedAccidentDateTime = (accidentDate && accidentDate !== 'N/A')
    ? `${accidentDate}${accidentTime ? ` ${accidentTime}` : ''}`
    : '';
  const dateTimeLabel = (hasAccidentParts && combinedAccidentDateTime)
    ? combinedAccidentDateTime
    : (timestampValid
        ? formatDateTimeHuman(timestampDate, combinedAccidentDateTime || 'N/A')
        : (combinedAccidentDateTime || 'N/A'));
  const matchCount = (props.match_count ?? props.matchCount) as number | null | undefined;
  const severity = props.severity || props.crashSeverity || props.SEVERITY || 'Unknown';
  const roadName = props.roadName || props.road_name || props.road || '';
  const latLon = `${coordinates[1].toFixed(4)}, ${coordinates[0].toFixed(4)}`;
  const actionHtml = includeAction
    ? `
        <button id="${buttonId}" style="
          width: 100%;
          margin-top: 8px;
          padding: 6px 10px;
          border-radius: 6px;
          border: 1px solid rgba(255, 107, 107, 0.6);
          background: rgba(255, 107, 107, 0.18);
          color: #ffb3b3;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.2px;
          cursor: pointer;
        ">Analyze this crash</button>
      `
    : `
        <div style="
          margin-top: 8px;
          font-size: 9px;
          color: rgba(255, 255, 255, 0.6);
          letter-spacing: 0.4px;
        ">Click for crash analysis</div>
      `;

  return `
    <div style="
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: linear-gradient(135deg, rgba(40, 10, 10, 0.98), rgba(60, 15, 15, 0.98));
      color: #fff;
      border: 1px solid rgba(255, 51, 51, 0.5);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 8px 32px rgba(255, 0, 0, 0.4);
      backdrop-filter: blur(10px);
      min-width: 220px;
    ">
      <div style="
        background: rgba(255, 51, 51, 0.2);
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255, 51, 51, 0.3);
        display: flex;
        align-items: center;
        gap: 8px;
      ">
        <div style="font-size: 24px;">🚨</div>
        <div>
          <div style="font-size: 14px; font-weight: 700; color: #ff6b6b;">CRASH</div>
          <div style="font-size: 9px; color: rgba(255, 255, 255, 0.6);">ACCIDENT REPORT</div>
        </div>
      </div>
      <div style="padding: 10px 12px;">
        <div style="font-size: 11px; line-height: 1.8; color: #ccd6f6;">
          ${severity ? `
            <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 6px;">
              <span style="opacity: 0.7;">Severity</span>
              <strong style="color: #ff6b6b;">${String(severity)}</strong>
            </div>
          ` : ''}
          ${props.hp_acc_image_no ? `
            <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 4px;">
              <span style="opacity: 0.7;">Report #</span>
              <strong style="font-size: 9px;">${String(props.hp_acc_image_no)}</strong>
            </div>
          ` : ''}
          <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 4px;">
            <span style="opacity: 0.7;">Date/Time</span>
            <strong>${dateTimeLabel}</strong>
          </div>
          ${roadName ? `
            <div style="display: flex; justify-content: space-between; font-size: 10px; margin-bottom: 4px;">
              <span style="opacity: 0.7;">Road</span>
              <strong style="font-size: 9px;">${String(roadName)}</strong>
            </div>
          ` : ''}
          <div style="display: flex; justify-content: space-between; font-size: 10px; margin-top: 6px;">
            <span style="opacity: 0.7;">Location</span>
            <strong style="font-size: 9px;">${latLon}</strong>
          </div>
          ${matchCount != null ? `
          <div style="display:flex; justify-content:space-between; font-size:10px; margin-bottom:4px; margin-top:4px;">
            <span style="opacity:0.7;">Matched events</span>
            <strong>${matchCount}</strong>
          </div>` : ''}
          ${actionHtml}
        </div>
      </div>
    </div>
  `;
};

export const buildWorkzonePopupHtml = (
  props: Record<string, unknown>,
  includeAction: boolean,
  buttonId?: string
) => {
  const roadName = props?.roadName || props?.road_name || 'Unknown';
  const status = String(props?.status || 'Unknown').toUpperCase();
  const start = formatDateTimeHuman(props?.startDate, 'N/A');
  const end = formatDateTimeHuman(props?.endDate, 'N/A');
  const description = props?.description || '';
  const identifier = props?.id ? `#${String(props.id)}` : 'Unknown ID';
  const actionHtml = includeAction
    ? `
        <button id="${buttonId}" style="
          width: 100%;
          margin-top: 8px;
          padding: 6px 10px;
          border-radius: 6px;
          border: 1px solid rgba(255, 213, 79, 0.6);
          background: rgba(255, 213, 79, 0.15);
          color: #ffe082;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.2px;
          cursor: pointer;
        ">Analyze this workzone</button>
      `
    : '';

  return `
    <div style="
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: linear-gradient(135deg, rgba(40, 35, 5, 0.98), rgba(60, 45, 10, 0.98));
      color: #fff;
      border: 1px solid rgba(255, 213, 79, 0.6);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: 0 8px 24px rgba(255, 213, 79, 0.4);
      min-width: 220px;
    ">
      <div style="
        background: rgba(255, 213, 79, 0.15);
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255, 213, 79, 0.3);
        display: flex;
        align-items: center;
        gap: 10px;
      ">
        <div style="font-size: 22px;">🚧</div>
        <div>
          <div style="font-size: 13px; font-weight: 700; color: #ffd54f;">WORKZONE</div>
          <div style="font-size: 10px; letter-spacing: 1px; color: rgba(255, 255, 255, 0.6);">${status}</div>
          <div style="font-size: 10px; color: rgba(255, 255, 255, 0.72); margin-top: 2px;">ID: ${identifier}</div>
        </div>
      </div>
      <div style="padding: 10px 12px;">
        <div style="font-size: 10px; text-transform: uppercase; color: rgba(255, 255, 255, 0.5); margin-bottom: 4px;">Road</div>
        <div style="font-size: 12px; font-weight: 600; color: #ffe082; margin-bottom: 8px;">${String(roadName)}</div>
        <div style="font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-bottom: 4px;">
          <strong>Start:</strong> ${start}
        </div>
        <div style="font-size: 10px; color: rgba(255, 255, 255, 0.7); margin-bottom: 6px;">
          <strong>End:</strong> ${end}
        </div>
        ${description ? `<div style="font-size: 10px; color: rgba(255, 255, 255, 0.75); border-top: 1px solid rgba(255, 213, 79, 0.2); padding-top: 6px;">${String(description)}</div>` : ''}
        ${actionHtml}
      </div>
    </div>
  `;
};

const formatCvSpeed = (value: unknown) => {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toFixed(1)} mph` : 'N/A';
};

const getCvRoadSpeedMetrics = (props: CvRoadAggregateProperties) => {
  const propsAny = props as Record<string, unknown>;
  const avgRaw = propsAny['avg_speed_mph'] ?? propsAny['avg_speed'];
  const limitRaw =
    propsAny['speed_limit_mph'] ??
    propsAny['speed_limit_mode'] ??
    propsAny['speed_limit_p50_mph'] ??
    propsAny['speed_limit'];
  const avg = Number(avgRaw);
  const limit = Number(limitRaw);
  const hasAvg = Number.isFinite(avg);
  const hasLimit = Number.isFinite(limit) && limit > 0;
  let color = '#9e9e9e';
  let label = 'No speed limit';
  if (hasAvg && hasLimit) {
    const signedDelta = avg - limit;
    if (signedDelta >= 10) {
      color = '#8b0000';
      label = '10+ mph over';
    } else if (signedDelta <= -10) {
      color = '#e53935';
      label = '10+ mph under';
    } else {
      color = '#2e7d32';
      label = 'Within 10 mph';
    }
  } else if (hasAvg) {
    label = 'Avg speed';
  }
  return { avg, limit, hasAvg, hasLimit, color, label };
};

const getCvRoadVehicleMetrics = (props: CvRoadAggregateProperties) => {
  const propsAny = props as Record<string, unknown>;
  const total = Number(propsAny['unique_vehicles_total']);
  const hourlyDistribution = parseHourlyUniqueVehicles(propsAny['hourly_unique_vehicles_json']);
  const avgFromHourly = computeAvgUniqueVehiclesPerHourFromHourlyJson(propsAny['hourly_unique_vehicles_json']);
  const avgFallback = Number(propsAny['avg_unique_vehicles_per_hour']);
  const avgPerHour = avgFromHourly ?? (Number.isFinite(avgFallback) ? avgFallback : Number.NaN);
  const hasTotal = Number.isFinite(total);
  const hasAvgPerHour = Number.isFinite(avgPerHour);
  const color = getRoadVehicleCountColor(hasTotal ? total : null);
  const label = hasTotal ? `${Math.max(0, Math.round(total)).toLocaleString()} vehicles` : 'No vehicle data';
  return { total, avgPerHour, hasTotal, hasAvgPerHour, color, label, hourlyDistribution };
};

const resolveRoadSegmentId = (props: CvRoadAggregateProperties): string => {
  const propsAny = props as Record<string, unknown>;
  const candidates = [
    props.road_segment_id,
    propsAny['segment_id'],
    props.way_id,
    propsAny['way_id'],
    propsAny['roadSegmentId'],
    propsAny['wayId'],
  ];
  for (const raw of candidates) {
    const value = String(raw ?? '').trim();
    if (!value) continue;
    const normalized = value.toLowerCase();
    if (normalized === 'unknown' || normalized === 'n/a' || normalized === 'null' || normalized === 'none') {
      continue;
    }
    return value;
  }
  return '';
};

const formatHourLabel = (hour: number): string => {
  const h = ((hour % 24) + 24) % 24;
  const suffix = h >= 12 ? ' PM' : ' AM';
  const base = h % 12 === 0 ? 12 : h % 12;
  return `${base}${suffix}`;
};

const buildHourlyDistributionSvg = (entries: Array<{ hour: number; value: number }>): string => {
  if (!entries.length) return '';
  const barWidth = 12;
  const gap = 2;
  const padX = 12;
  const height = 56;
  const labelArea = 16;
  const width = padX * 2 + 24 * barWidth + 23 * gap;
  const valueByHour = new Map(entries.map((entry) => [entry.hour, entry.value]));
  const max = Math.max(...entries.map((entry) => entry.value), 1);
  const bars = Array.from({ length: 24 }, (_, hour) => {
    const value = valueByHour.get(hour) ?? 0;
    const scaled = Math.max(0, Math.round((value / max) * height));
    const x = padX + hour * (barWidth + gap);
    const y = height - scaled;
    const opacity = value > 0 ? 0.9 : 0.22;
    const hourLabel = formatHourLabel(hour);
    const countLabel = Math.round(value).toLocaleString();
    const tooltip = `${hourLabel}: ${countLabel} vehicles`;
    return `<rect class="road-detail-chart-bar" data-tooltip="${tooltip}" x="${x}" y="${y}" width="${barWidth}" height="${scaled}" rx="2" fill="#64ffda" opacity="${opacity}" style="cursor:pointer;"></rect>`;
  }).join('');
  const hourLabels = [
    { hour: 0, label: '12AM' },
    { hour: 3, label: '3AM' },
    { hour: 6, label: '6AM' },
    { hour: 9, label: '9AM' },
    { hour: 12, label: '12PM' },
    { hour: 15, label: '3PM' },
    { hour: 18, label: '6PM' },
    { hour: 21, label: '9PM' },
  ];
  const labels = hourLabels.map(({ hour, label }) => {
    const x = padX + hour * (barWidth + gap) + barWidth / 2;
    return `<text x="${x}" y="${height + 11}" text-anchor="middle" fill="rgba(255,255,255,0.7)" font-size="7.8" font-weight="500">${label}</text>`;
  }).join('');
  return `<svg class="road-detail-hourly-chart" viewBox="0 0 ${width} ${height + labelArea}" preserveAspectRatio="xMidYMid meet">
    <g transform="translate(0, 2)">${bars}</g>
    ${labels}
  </svg>`;
};

export const buildCvRoadHoverHtml = (
  props: CvRoadAggregateProperties,
  mode: RoadColorMode = 'speed-compliance',
) => {
  const { avg, limit, hasAvg, hasLimit, color: speedColor, label: speedLabel } = getCvRoadSpeedMetrics(props);
  const {
    total,
    avgPerHour,
    hasTotal,
    hasAvgPerHour,
    color: vehicleColor,
    label: vehicleLabel,
    hourlyDistribution,
  } = getCvRoadVehicleMetrics(props);
  const accentColor = mode === 'vehicle-count' ? vehicleColor : speedColor;
  const statusLabel = mode === 'vehicle-count' ? vehicleLabel : speedLabel;
  const title = mode === 'vehicle-count' ? 'Road Vehicles' : 'Road Speed';
  const propsAny = props as Record<string, unknown>;
  const roadName = (
    props.road_name ||
    props.label ||
    props.ref ||
    props.name ||
    props.highway ||
    (propsAny['roadName'] as string | undefined) ||
    (props.way_id != null ? `Way ${String(props.way_id)}` : 'Unknown')
  ) as string;
  const segmentId = resolveRoadSegmentId(props);
  const pointCount = props.point_count ?? propsAny['points'] ?? propsAny['pointCount'];
  const pointsLabel = Number.isFinite(Number(pointCount)) ? Number(pointCount).toLocaleString() : '—';
  const peakHour = hourlyDistribution.reduce<{ hour: number; value: number } | null>((best, entry) => {
    if (!best || entry.value > best.value) return entry;
    return best;
  }, null);

  return `
    <div style="
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: linear-gradient(135deg, rgba(10, 14, 39, 0.98), rgba(20, 25, 50, 0.98));
      color: #fff;
      border: 1px solid ${accentColor};
      border-left: 5px solid ${accentColor};
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
      min-width: 200px;
    ">
      <div style="
        background: rgba(255,255,255,0.04);
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      ">
        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: rgba(255,255,255,0.6);">${title}</div>
        <div style="font-size: 10px; color: ${accentColor}; font-weight: 600;">${statusLabel}</div>
      </div>
      <div style="padding: 8px 10px; font-size: 11px; line-height: 1.7;">
        <div style="font-size: 12px; font-weight: 700; color: #e6f1ff; margin-bottom: 4px;">${roadName}</div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Segment ID</span><strong>${segmentId || 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Avg speed</span><strong>${hasAvg ? formatCvSpeed(avg) : 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Speed limit</span><strong>${hasLimit ? formatCvSpeed(limit) : 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Unique Vehicles</span><strong>${hasTotal ? Math.max(0, Math.round(total)).toLocaleString() : 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Avg Vehicles/Hr</span><strong>${hasAvgPerHour ? avgPerHour.toFixed(1) : 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Peak Hour</span><strong>${peakHour ? formatHourLabel(peakHour.hour) : 'N/A'}</strong></div>
        <div style="display:flex; justify-content:space-between;"><span style="opacity:0.7;">Points</span><strong>${pointsLabel}</strong></div>
      </div>
    </div>
  `;
};

const buildSpeedSparkline = (avg: number, limit: number, p50: number, p90: number, hasLimit: boolean) => {
  if (!Number.isFinite(avg)) return '';
  const values = [
    { label: 'Avg', tooltipLabel: 'Average speed', val: avg, color: '#64ffda' },
    ...(hasLimit ? [{ label: 'Lim', tooltipLabel: 'Speed limit', val: limit, color: '#f59e0b' }] : []),
    ...(Number.isFinite(p50) ? [{ label: 'P50', tooltipLabel: 'P50 speed', val: p50, color: '#3b82f6' }] : []),
    ...(Number.isFinite(p90) ? [{ label: 'P90', tooltipLabel: 'P90 speed', val: p90, color: '#ef4444' }] : []),
  ];
  if (values.length < 2) return '';
  const max = Math.max(...values.map((v) => v.val), 1) * 1.1;
  const barW = 16;
  const gap = 9;
  const barMaxH = 56;
  const padX = 6;
  const labelY = barMaxH + 12;
  const chartW = padX * 2 + values.length * barW + (values.length - 1) * gap;
  const chartH = barMaxH + 15;
  const bars = values.map((v, i) => {
    const h = Math.max(6, (v.val / max) * barMaxH);
    const x = padX + i * (barW + gap);
    const tooltip = `${v.tooltipLabel}: ${v.val.toFixed(1)} mph`;
    return `<rect class="road-detail-chart-bar" data-tooltip="${tooltip}" x="${x}" y="${barMaxH - h}" width="${barW}" height="${h}" rx="2.5" fill="${v.color}" opacity="0.9" style="cursor:pointer;"></rect>
      <text x="${x + barW / 2}" y="${labelY}" text-anchor="middle" fill="rgba(255,255,255,0.7)" font-size="8.6">${v.label}</text>`;
  }).join('');
  return `<svg class="road-detail-speed-sparkline" viewBox="0 0 ${chartW} ${chartH}" preserveAspectRatio="xMidYMid meet">${bars}</svg>`;
};

const toCount = (value: unknown): number | null => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.round(numeric));
};

export const buildCvRoadPopupHtml = (
  props: CvRoadAggregateProperties,
  mode: RoadColorMode = 'speed-compliance',
) => {
  const { avg, limit, hasAvg, hasLimit, color: speedColor, label: speedLabel } = getCvRoadSpeedMetrics(props);
  const {
    total,
    avgPerHour,
    hasTotal,
    hasAvgPerHour,
    color: vehicleColor,
    label: vehicleLabel,
    hourlyDistribution,
  } = getCvRoadVehicleMetrics(props);
  const accentColor = mode === 'vehicle-count' ? vehicleColor : speedColor;
  const statusLabel = mode === 'vehicle-count' ? vehicleLabel : speedLabel;
  const propsAny = props as Record<string, unknown>;
  const areaAnalysisActive =
    propsAny['area_analysis'] === true ||
    propsAny['area_analysis'] === 'true' ||
    propsAny['from_area_analysis'] === true;
  const roadName = props.road_name || props.name || (propsAny['roadName'] as string | undefined) || 'Unknown';
  const segmentId = resolveRoadSegmentId(props);
  const roadTitle = segmentId ? `${roadName} (${segmentId})` : roadName;
  const count = typeof props.point_count === 'number' ? props.point_count.toLocaleString() : 'N/A';
  const p50SpeedVal = propsAny['p50_speed_mph'] ?? propsAny['p50_speed'];
  const p90SpeedVal = propsAny['p90_speed_mph'] ?? propsAny['p90_speed'];
  const crashCountVal = propsAny['crash_count'] ?? propsAny['crashes'];
  const hardBrakeCountVal = propsAny['hard_brake_count'] ?? propsAny['hard_brakes'];
  const crashCount = toCount(crashCountVal);
  const hardBrakeCount = toCount(hardBrakeCountVal);
  const p50Speed = formatCvSpeed(p50SpeedVal);
  const p90Speed = formatCvSpeed(p90SpeedVal);
  const avgSpeed = hasAvg ? formatCvSpeed(avg) : 'N/A';
  const speedLimit = hasLimit ? formatCvSpeed(limit) : 'N/A';
  const sparkline = buildSpeedSparkline(avg, limit, Number(p50SpeedVal), Number(p90SpeedVal), hasLimit);
  const tertiaryMetricLabel = areaAnalysisActive ? 'Crash Count' : 'P50 Speed';
  const tertiaryMetricValue = areaAnalysisActive ? (crashCount ?? 0).toLocaleString() : p50Speed;
  const quaternaryMetricLabel = areaAnalysisActive ? 'Hard Brakes' : 'P90 Speed';
  const quaternaryMetricValue = areaAnalysisActive ? (hardBrakeCount ?? 0).toLocaleString() : p90Speed;
  const peakHour = hourlyDistribution.reduce<{ hour: number; value: number } | null>((best, entry) => {
    if (!best || entry.value > best.value) return entry;
    return best;
  }, null);
  const hourlyDistributionSvg = buildHourlyDistributionSvg(hourlyDistribution);
  const primaryLabel = 'Average Speed';
  const primaryValue = avgSpeed;
  const secondaryLabel = 'Speed Limit';
  const secondaryValue = speedLimit;
  const peakHourValue = peakHour ? formatHourLabel(peakHour.hour) : 'N/A';
  const peakHourNote = peakHour ? `${Math.round(peakHour.value).toLocaleString()} vehicles` : '';

  return `
    <div class="road-detail-card" style="--road-accent: ${accentColor};">
      <div class="road-detail-header">
        <div class="road-detail-title-wrap">
          <div class="road-detail-title">${areaAnalysisActive ? 'Area Road Stats' : 'Road Stats'}</div>
          <div class="road-detail-subtitle">${roadTitle}</div>
        </div>
        <div class="road-detail-header-actions">
          <div class="road-detail-status">${statusLabel}</div>
        </div>
      </div>
      <div class="road-detail-body">
        <div class="road-detail-stats">
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">${primaryLabel}</span>
            <span class="road-detail-stat-value">${primaryValue}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">${secondaryLabel}</span>
            <span class="road-detail-stat-value">${secondaryValue}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">${tertiaryMetricLabel}</span>
            <span class="road-detail-stat-value">${tertiaryMetricValue}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">${quaternaryMetricLabel}</span>
            <span class="road-detail-stat-value">${quaternaryMetricValue}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">Segment ID</span>
            <span class="road-detail-stat-value">${segmentId || 'N/A'}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">Points</span>
            <span class="road-detail-stat-value">${count}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">Unique Vehicles</span>
            <span class="road-detail-stat-value">${hasTotal ? Math.max(0, Math.round(total)).toLocaleString() : 'N/A'}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">Avg Vehicles/Hr</span>
            <span class="road-detail-stat-value">${hasAvgPerHour ? avgPerHour.toFixed(1) : 'N/A'}</span>
          </div>
          <div class="road-detail-stat-row">
            <span class="road-detail-stat-label">Peak Hour</span>
            <span class="road-detail-stat-value">${peakHourValue}${peakHourNote ? ` <span class="road-detail-stat-note">(${peakHourNote})</span>` : ''}</span>
          </div>
        </div>
        <div class="road-detail-chart-area">
          <div class="road-detail-item-label">Speed Graph</div>
          ${sparkline || '<div class="road-detail-speed-empty">No speed profile</div>'}
        </div>
      </div>
      ${hourlyDistributionSvg ? `
      <div class="road-detail-hourly">
        <div class="road-detail-item-label">Hourly Distribution</div>
        ${hourlyDistributionSvg}
      </div>` : ''}
    </div>
  `;
};

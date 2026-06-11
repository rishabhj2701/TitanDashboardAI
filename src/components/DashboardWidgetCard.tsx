import { useState, useCallback, useRef, useEffect } from 'react';
import type { DashboardWidget, RoadStatsWidget } from '../types/dashboard';
import type { GeneratedChartPayload } from '../types/charts';
import {
  getSummaryStats,
  getSpeedCompliance,
  getTopSpeedingRoads,
  getHourlyTrend,
  getCountyBreakdown,
  getFuncClassStats,
  getSpeedDistribution,
  getTopRoadsVolume,
  getDayOfWeekTrend,
  getSpeedVsLimit,
  getHourlyVehicleCounts,
} from '../api/dataQualityClient';
import ChartRenderer from './ChartRenderer';
import DataQualityPanel from './DataQualityPanel';
import { computeAvgUniqueVehiclesPerHourFromHourlyJson } from '../utils/hourlyVehicles';

interface MapStats {
  total: number;
  cvPoints: number;
  crashes: number;
  hardBraking: number;
  avgSpeed: number;
  maxSpeed: number;
}

/* ---------- Loading Skeleton ---------- */
function WidgetSkeleton() {
  return (
    <div className="widget-skeleton-container">
      <div className="widget-skeleton widget-skeleton-bar" style={{ width: '75%', height: 14 }} />
      <div className="widget-skeleton widget-skeleton-bar" style={{ width: '50%', height: 14 }} />
      <div className="widget-skeleton widget-skeleton-block" />
      <div className="widget-skeleton widget-skeleton-bar" style={{ width: '60%', height: 10 }} />
    </div>
  );
}

const WIDGET_COLORS = ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#8b5cf6', '#ec4899'];

interface DashboardWidgetCardProps {
  widget: DashboardWidget;
  onRemove: () => void;
  onUpdateTitle: (title: string) => void;
  onToggleLock: () => void;
  onToggleCollapse: () => void;
  onFullscreen?: () => void;
  onSetColorTag?: (color: string | undefined) => void;
  onSetNote?: (note: string) => void;
  onSetSize?: (preset: 'small' | 'medium' | 'large') => void;
  onMoveTo?: (targetDashboardId: string) => void;
  otherDashboards?: { id: string; name: string }[];
  refreshTick?: number;
}

/* ---------- Road Stats Content ---------- */
function SpeedBarChart({ avg, limit, p50, p90 }: { avg: number | null; limit: number | null; p50: number | null; p90: number | null }) {
  const bars: Array<{ label: string; val: number; color: string }> = [];
  if (avg != null && Number.isFinite(avg)) bars.push({ label: 'Avg', val: avg, color: '#64ffda' });
  if (limit != null && Number.isFinite(limit) && limit > 0) bars.push({ label: 'Limit', val: limit, color: '#f59e0b' });
  if (p50 != null && Number.isFinite(p50)) bars.push({ label: 'Median', val: p50, color: '#3b82f6' });
  if (p90 != null && Number.isFinite(p90)) bars.push({ label: '90th %', val: p90, color: '#ef4444' });
  if (bars.length < 2) return null;
  const max = Math.max(...bars.map(b => b.val)) * 1.1;
  const barW = Math.floor(140 / bars.length);
  const barMaxH = 60;
  const totalW = bars.length * (barW + 6) + 8;
  return (
    <div style={{ marginTop: 4, marginBottom: 4 }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', color: 'rgba(255,255,255,0.5)', marginBottom: 4, letterSpacing: 0.5 }}>Speed Comparison</div>
      <svg width={totalW} height={barMaxH + 22} viewBox={`0 0 ${totalW} ${barMaxH + 22}`} style={{ display: 'block', width: '100%', height: 'auto' }}>
        {bars.map((b, i) => {
          const h = Math.max(6, (b.val / max) * barMaxH);
          const x = i * (barW + 6) + 4;
          return (
            <g key={b.label}>
              <rect x={x} y={barMaxH + 4 - h} width={barW} height={h} rx={3} fill={b.color} opacity={0.85} style={{ cursor: 'pointer' }}>
                <title>{b.label}: {b.val.toFixed(1)} mph</title>
              </rect>
              <text x={x + barW / 2} y={barMaxH + 16} textAnchor="middle" fill="rgba(255,255,255,0.6)" fontSize={9}>{b.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function HourlyBarChart({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data)
    .map(([h, v]) => ({ hour: Number(h), value: Number(v) }))
    .filter(e => Number.isInteger(e.hour) && e.hour >= 0 && e.hour <= 23 && Number.isFinite(e.value))
    .sort((a, b) => a.hour - b.hour);
  if (!entries.length) return null;
  const valueByHour = new Map(entries.map(e => [e.hour, e.value]));
  const max = Math.max(...entries.map(e => e.value), 1);
  const barWidth = 10;
  const gap = 2;
  const height = 50;
  const width = 24 * (barWidth + gap);
  const hourLabels = [
    { hour: 0, label: '12AM' }, { hour: 3, label: '3AM' }, { hour: 6, label: '6AM' }, { hour: 9, label: '9AM' },
    { hour: 12, label: '12PM' }, { hour: 15, label: '3PM' }, { hour: 18, label: '6PM' }, { hour: 21, label: '9PM' },
  ];
  const fmtHour = (h: number) => { const s = h >= 12 ? ' PM' : ' AM'; const b = h % 12 === 0 ? 12 : h % 12; return `${b}${s}`; };
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', color: 'rgba(255,255,255,0.5)', marginBottom: 4, letterSpacing: 0.5 }}>Hourly Distribution</div>
      <svg width={width} height={height + 22} viewBox={`0 0 ${width} ${height + 22}`} style={{ display: 'block', width: '100%', height: 'auto' }}>
        <g transform="translate(0, 2)">
          {Array.from({ length: 24 }, (_, hour) => {
            const value = valueByHour.get(hour) ?? 0;
            const scaled = Math.max(0, Math.round((value / max) * height));
            const x = hour * (barWidth + gap);
            const y = height - scaled;
            const opacity = value > 0 ? 0.9 : 0.22;
            return (
              <rect key={hour} x={x} y={y} width={barWidth} height={scaled} rx={2} fill="#64ffda" opacity={opacity} style={{ cursor: 'pointer' }}>
                <title>{fmtHour(hour)}: {Math.round(value)} vehicles</title>
              </rect>
            );
          })}
        </g>
        {hourLabels.map(({ hour, label }) => (
          <text key={hour} x={hour * (barWidth + gap) + barWidth / 2} y={height + 14} textAnchor="middle" fill="rgba(255,255,255,0.7)" fontSize={8} fontWeight={500}>{label}</text>
        ))}
      </svg>
    </div>
  );
}

function RoadStatsContent({ widget }: { widget: RoadStatsWidget }) {
  const { roadData } = widget;
  const fmt = (v: number | null | undefined) =>
    v != null && Number.isFinite(v) ? `${v.toFixed(1)} mph` : '--';
  const fmtCount = (v: number | null | undefined) => {
    const numeric = Number(v);
    return Number.isFinite(numeric) ? Math.max(0, Math.round(numeric)).toLocaleString() : '0';
  };
  const areaAnalysisMode = Boolean(roadData.area_analysis);
  const tertiaryLabel = areaAnalysisMode ? 'Crash Count' : 'Median Speed';
  const tertiaryValue = areaAnalysisMode ? fmtCount(roadData.crash_count) : fmt(roadData.p50_speed_mph);
  const quaternaryLabel = areaAnalysisMode ? 'Hard Brakes' : '90th % Speed';
  const quaternaryValue = areaAnalysisMode ? fmtCount(roadData.hard_brake_count) : fmt(roadData.p90_speed_mph);

  const avg = roadData.avg_speed_mph;
  const limit = roadData.speed_limit_mph;
  let statusColor = '#9e9e9e';
  let statusLabel = 'No limit data';
  if (avg != null && limit != null && limit > 0) {
    const delta = avg - limit;
    if (delta >= 10) { statusColor = '#ef4444'; statusLabel = '10+ mph over'; }
    else if (delta <= -10) { statusColor = '#f59e0b'; statusLabel = '10+ mph under'; }
    else { statusColor = '#22c55e'; statusLabel = 'Within 10 mph'; }
  }

  const uniqueVehicles = roadData.unique_vehicles_total;
  const avgVehPerHourFromJson = computeAvgUniqueVehiclesPerHourFromHourlyJson(roadData.hourly_unique_vehicles_json);
  const avgVehPerHour = avgVehPerHourFromJson ?? (
    roadData.avg_unique_vehicles_per_hour != null && Number.isFinite(roadData.avg_unique_vehicles_per_hour)
      ? roadData.avg_unique_vehicles_per_hour
      : null
  );

  return (
    <div className="road-stats-widget">
      <div className="road-stats-status" style={{ color: statusColor }}>{statusLabel}</div>
      <SpeedBarChart avg={avg} limit={limit} p50={roadData.p50_speed_mph ?? null} p90={roadData.p90_speed_mph ?? null} />
      <div className="road-stats-grid">
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">{fmt(roadData.avg_speed_mph)}</span>
          <span className="dashboard-stat-label">Average Speed</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">{fmt(roadData.speed_limit_mph)}</span>
          <span className="dashboard-stat-label">Speed Limit</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">{tertiaryValue}</span>
          <span className="dashboard-stat-label">{tertiaryLabel}</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">{quaternaryValue}</span>
          <span className="dashboard-stat-label">{quaternaryLabel}</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">
            {uniqueVehicles != null && Number.isFinite(uniqueVehicles) ? Math.round(uniqueVehicles).toLocaleString() : '--'}
          </span>
          <span className="dashboard-stat-label">Unique Vehicles</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">
            {avgVehPerHour != null && Number.isFinite(avgVehPerHour) ? avgVehPerHour.toFixed(1) : '--'}
          </span>
          <span className="dashboard-stat-label">Avg Vehicles/Hr</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">{roadData.peak_hour || '--'}</span>
          <span className="dashboard-stat-label">Peak Hour</span>
        </div>
        <div className="dashboard-stat-item">
          <span className="dashboard-stat-value">
            {roadData.point_count != null ? roadData.point_count.toLocaleString() : '--'}
          </span>
          <span className="dashboard-stat-label">Data Points</span>
        </div>
      </div>
      {roadData.hourly_unique_vehicles_json && (
        <HourlyBarChart data={roadData.hourly_unique_vehicles_json} />
      )}
      {roadData.road_segment_id && (
        <div className="road-stats-segment-id">Segment: {roadData.road_segment_id}</div>
      )}
    </div>
  );
}

/* ---------- Stat Summary Content ---------- */
function StatSummaryContent({ refreshTick }: { refreshTick?: number }) {
  const [stats, setStats] = useState<MapStats>({
    total: 0, cvPoints: 0, crashes: 0, hardBraking: 0, avgSpeed: 0, maxSpeed: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSummaryStats()
      .then((data) => {
        if (!cancelled) {
          setStats({
            total: data.total ?? 0, cvPoints: data.cvPoints ?? 0,
            crashes: data.crashes ?? 0, hardBraking: data.hardBraking ?? 0,
            avgSpeed: data.avgSpeed ?? 0, maxSpeed: data.maxSpeed ?? 0,
          });
        }
      })
      .catch((err) => console.error('summary stats fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading) {
    return <WidgetSkeleton />;
  }

  return (
    <div className="dashboard-stats-grid">
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.total.toLocaleString()}</span>
        <span className="dashboard-stat-label">Total Points</span>
      </div>
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.cvPoints.toLocaleString()}</span>
        <span className="dashboard-stat-label">CV Points</span>
      </div>
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.avgSpeed || '--'}</span>
        <span className="dashboard-stat-label">Avg Speed (mph)</span>
      </div>
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.maxSpeed || '--'}</span>
        <span className="dashboard-stat-label">Max Speed (mph)</span>
      </div>
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.crashes.toLocaleString()}</span>
        <span className="dashboard-stat-label">Crashes</span>
      </div>
      <div className="dashboard-stat-item">
        <span className="dashboard-stat-value">{stats.hardBraking.toLocaleString()}</span>
        <span className="dashboard-stat-label">Hard Braking</span>
      </div>
    </div>
  );
}

/* ---------- Speed Compliance Content ---------- */
function SpeedComplianceContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSpeedCompliance()
      .then((data) => {
        if (cancelled) return;
        setPayload({
          type: 'doughnut',
          xValues: ['Within Limit', 'Over Limit', 'No Data'],
          series: [{ label: 'Speed Compliance', values: [data.within_limit, data.over_limit, data.no_limit_data] }],
        });
      })
      .catch((err) => console.error('speed compliance fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Speed Compliance" variant="preview" /></div>;
}

/* ---------- Top Speeding Roads Content ---------- */
function TopSpeedingRoadsContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getTopSpeedingRoads(10)
      .then((data) => {
        if (cancelled) return;
        const roads = data.roads || [];
        setPayload({
          type: 'bar', orientation: 'horizontal',
          xLabel: 'MPH Over Limit', yLabel: 'Road',
          xValues: roads.map(r => r.road_name || 'Unknown'),
          series: [{ label: 'Speed Over Limit', values: roads.map(r => Math.round((r.speed_over_limit ?? 0) * 10) / 10) }],
        });
      })
      .catch((err) => console.error('top speeding roads fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Top Speeding Roads" variant="preview" /></div>;
}

/* ---------- Hourly Trend Content ---------- */
function HourlyTrendContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getHourlyTrend()
      .then((data) => {
        if (cancelled) return;
        const hours = data.hours || [];
        const allHours = Array.from({ length: 24 }, (_, i) => i);
        const hourMap = new Map(hours.map(h => [h.hour, h]));
        setPayload({
          type: 'bar', xLabel: 'Hour of Day', yLabel: 'Avg Speed (mph)',
          xValues: allHours.map(h => `${h}:00`),
          series: [{ label: 'Avg Speed', values: allHours.map(h => hourMap.get(h)?.avg_speed ?? 0) }],
        });
      })
      .catch((err) => console.error('hourly trend fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Hourly Speed Trend" variant="preview" /></div>;
}

/* ---------- Hourly Vehicle Count Content ---------- */
function HourlyVehicleCountContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getHourlyVehicleCounts()
      .then((data) => {
        if (cancelled) return;
        const hours = data.hours || [];
        const allHours = Array.from({ length: 24 }, (_, i) => i);
        const hourMap = new Map(hours.map(h => [h.hour, h]));
        setPayload({
          type: 'bar', xLabel: 'Hour of Day', yLabel: 'Unique Vehicles',
          xValues: allHours.map(h => `${h}:00`),
          series: [{ label: 'Unique Vehicles', values: allHours.map(h => hourMap.get(h)?.total_vehicles ?? 0) }],
        });
      })
      .catch((err) => console.error('hourly vehicle counts fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Hourly Vehicle Count" variant="preview" /></div>;
}

/* ---------- County Breakdown Content ---------- */
function CountyBreakdownContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getCountyBreakdown()
      .then((data) => {
        if (cancelled) return;
        const counties = data.counties || [];
        setPayload({
          type: 'dual_axis', xLabel: 'County', leftLabel: 'Point Count', rightLabel: 'Avg Speed (mph)',
          xValues: counties.map(c => c.county),
          series: [
            { label: 'Point Count', values: counties.map(c => c.point_count), axis: 'left' },
            { label: 'Avg Speed', values: counties.map(c => c.avg_speed), axis: 'right' },
          ],
        });
      })
      .catch((err) => console.error('county breakdown fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="County Breakdown" variant="preview" /></div>;
}

/* ---------- Functional Class Stats Content ---------- */
function FuncClassStatsContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getFuncClassStats()
      .then((data) => {
        if (cancelled) return;
        const classes = data.classes || [];
        setPayload({
          type: 'dual_axis', xLabel: 'Functional Class', leftLabel: 'Point Count', rightLabel: 'Avg Speed (mph)',
          xValues: classes.map(c => c.func_class),
          series: [
            { label: 'Point Count', values: classes.map(c => c.point_count), axis: 'left' },
            { label: 'Avg Speed', values: classes.map(c => c.avg_speed), axis: 'right' },
          ],
        });
      })
      .catch((err) => console.error('func class stats fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Functional Class Stats" variant="preview" /></div>;
}

/* ---------- Speed Distribution Content ---------- */
function SpeedDistributionContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSpeedDistribution(10)
      .then((data) => {
        if (cancelled) return;
        const buckets = data.buckets || [];
        setPayload({
          type: 'bar', xLabel: 'Speed Range (mph)', yLabel: 'Point Count',
          xValues: buckets.map(b => `${b.bucket_min}-${b.bucket_max}`),
          series: [{ label: 'Points', values: buckets.map(b => b.point_count) }],
        });
      })
      .catch((err) => console.error('speed distribution fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Speed Distribution" variant="preview" /></div>;
}

/* ---------- Top Roads by Volume Content ---------- */
function TopRoadsVolumeContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getTopRoadsVolume(15)
      .then((data) => {
        if (cancelled) return;
        const roads = data.roads || [];
        setPayload({
          type: 'bar', orientation: 'horizontal',
          xLabel: 'Data Points', yLabel: 'Road',
          xValues: roads.map(r => r.road_name || 'Unknown'),
          series: [{ label: 'Point Count', values: roads.map(r => r.point_count) }],
        });
      })
      .catch((err) => console.error('top roads volume fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Top Roads by Volume" variant="preview" /></div>;
}

/* ---------- Day of Week Trend Content ---------- */
const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function DayOfWeekTrendContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDayOfWeekTrend()
      .then((data) => {
        if (cancelled) return;
        const days = data.days || [];
        const dayMap = new Map(days.map(d => [d.dow, d]));
        const allDays = [1, 2, 3, 4, 5, 6, 7];
        setPayload({
          type: 'dual_axis', xLabel: 'Day of Week', leftLabel: 'Point Count', rightLabel: 'Avg Speed (mph)',
          xValues: allDays.map(d => DOW_LABELS[d - 1]),
          series: [
            { label: 'Point Count', values: allDays.map(d => dayMap.get(d)?.point_count ?? 0), axis: 'left' },
            { label: 'Avg Speed', values: allDays.map(d => dayMap.get(d)?.avg_speed ?? 0), axis: 'right' },
          ],
        });
      })
      .catch((err) => console.error('day of week trend fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Day of Week Trend" variant="preview" /></div>;
}

/* ---------- Speed vs Limit Content ---------- */
function SpeedVsLimitContent({ refreshTick }: { refreshTick?: number }) {
  const [payload, setPayload] = useState<GeneratedChartPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSpeedVsLimit(20)
      .then((data) => {
        if (cancelled) return;
        const roads = data.roads || [];
        setPayload({
          type: 'dual_axis', xLabel: 'Road', leftLabel: 'Speed (mph)', rightLabel: 'Speed (mph)',
          xValues: roads.map(r => r.road_name || 'Unknown'),
          series: [
            { label: 'Avg Speed', values: roads.map(r => r.avg_speed_mph), axis: 'left' },
            { label: 'Speed Limit', values: roads.map(r => r.speed_limit_mph), axis: 'left' },
          ],
        });
      })
      .catch((err) => console.error('speed vs limit fetch failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refreshTick]);

  if (loading || !payload) return <WidgetSkeleton />;
  return <div className="dashboard-chart-container"><ChartRenderer payload={payload} title="Speed vs Speed Limit" variant="preview" /></div>;
}

/* ---------- Editable Title ---------- */
function EditableTitle({ title, onChange }: { title: string; onChange: (t: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const commit = useCallback(() => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== title) onChange(trimmed);
    else setDraft(title);
    setEditing(false);
  }, [draft, title, onChange]);

  if (!editing) {
    return (
      <span className="dashboard-widget-title" onDoubleClick={() => { setDraft(title); setEditing(true); }} title="Double-click to rename">
        {title}
      </span>
    );
  }
  return (
    <input ref={inputRef} className="dashboard-widget-title-input" value={draft}
      onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(title); setEditing(false); } }}
    />
  );
}

/* ---------- Widget Body (shared renderer) ---------- */
function WidgetBody({ widget, refreshTick }: { widget: DashboardWidget; refreshTick?: number }) {
  switch (widget.type) {
    case 'chart':
      return <div className="dashboard-chart-container"><ChartRenderer payload={widget.chartPayload} title={widget.title} variant="preview" /></div>;
    case 'stat-summary':
      return <StatSummaryContent refreshTick={refreshTick} />;
    case 'data-quality':
      return <DataQualityPanel />;
    case 'road-stats':
      return <RoadStatsContent widget={widget} />;
    case 'speed-compliance':
      return <SpeedComplianceContent refreshTick={refreshTick} />;
    case 'top-speeding-roads':
      return <TopSpeedingRoadsContent refreshTick={refreshTick} />;
    case 'hourly-trend':
      return <HourlyTrendContent refreshTick={refreshTick} />;
    case 'county-breakdown':
      return <CountyBreakdownContent refreshTick={refreshTick} />;
    case 'func-class-stats':
      return <FuncClassStatsContent refreshTick={refreshTick} />;
    case 'speed-distribution':
      return <SpeedDistributionContent refreshTick={refreshTick} />;
    case 'top-roads-volume':
      return <TopRoadsVolumeContent refreshTick={refreshTick} />;
    case 'day-of-week-trend':
      return <DayOfWeekTrendContent refreshTick={refreshTick} />;
    case 'speed-vs-limit':
      return <SpeedVsLimitContent refreshTick={refreshTick} />;
    case 'hourly-vehicle-count':
      return <HourlyVehicleCountContent refreshTick={refreshTick} />;
    default:
      return null;
  }
}

/* ---------- Main Widget Card ---------- */
function DashboardWidgetCard({
  widget, onRemove, onUpdateTitle, onToggleLock, onToggleCollapse, onFullscreen,
  onSetColorTag, onSetNote, onSetSize, onMoveTo, otherDashboards, refreshTick,
}: DashboardWidgetCardProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [showMenu, setShowMenu] = useState(false);
  const [editingNote, setEditingNote] = useState(false);
  const [noteDraft, setNoteDraft] = useState(widget.note || '');
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Track last updated time via refreshTick
  useEffect(() => { setLastUpdated(Date.now()); }, [refreshTick]);

  // Close menu on outside click
  useEffect(() => {
    if (!showMenu) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowMenu(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showMenu]);

  const handleExportPNG = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const canvas = el.querySelector('canvas');
    if (!canvas) return;
    const link = document.createElement('a');
    link.download = `${widget.title.replace(/\s+/g, '_')}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    setShowMenu(false);
  }, [widget.title]);

  const commitNote = useCallback(() => {
    onSetNote?.(noteDraft);
    setEditingNote(false);
    setShowMenu(false);
  }, [noteDraft, onSetNote]);

  const agoLabel = lastUpdated
    ? (() => { const s = Math.round((Date.now() - lastUpdated) / 1000); return s < 5 ? 'just now' : s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`; })()
    : null;

  return (
    <div className={`dashboard-widget widget-fade-in${widget.collapsed ? ' collapsed' : ''}`}>
      {/* Color tag strip */}
      {widget.colorTag && <div className="widget-color-tag" style={{ background: widget.colorTag }} />}

      <div className="dashboard-widget-header">
        <span className="dashboard-widget-drag-handle" title="Drag to reorder">&#x2807;&#x2807;</span>
        <EditableTitle title={widget.title} onChange={onUpdateTitle} />
        <div className="dashboard-widget-header-actions">
          <button className="dashboard-widget-action-btn" onClick={onToggleCollapse} title={widget.collapsed ? 'Expand' : 'Collapse'}>
            {widget.collapsed ? '\u25BC' : '\u25B2'}
          </button>
          {onFullscreen && (
            <button className="dashboard-widget-action-btn" onClick={onFullscreen} title="Fullscreen">&#x26F6;</button>
          )}
          <button className={`dashboard-widget-action-btn ${widget.locked ? 'active' : ''}`} onClick={onToggleLock} title={widget.locked ? 'Unlock' : 'Lock'}>
            {widget.locked ? '\u{1F512}' : '\u{1F513}'}
          </button>

          {/* Overflow menu */}
          <div className="widget-menu-wrapper" ref={menuRef}>
            <button className="dashboard-widget-action-btn" onClick={() => setShowMenu(!showMenu)} title="More options">&#x22EF;</button>
            {showMenu && (
              <div className="widget-overflow-menu">
                <button className="widget-menu-item" onClick={handleExportPNG}>Export PNG</button>
                <button className="widget-menu-item" onClick={() => { onSetSize?.('small'); setShowMenu(false); }}>Resize: S</button>
                <button className="widget-menu-item" onClick={() => { onSetSize?.('medium'); setShowMenu(false); }}>Resize: M</button>
                <button className="widget-menu-item" onClick={() => { onSetSize?.('large'); setShowMenu(false); }}>Resize: L</button>
                <div className="widget-menu-divider" />
                <div className="widget-menu-colors">
                  {WIDGET_COLORS.map(c => (
                    <button key={c} className={`widget-color-dot${widget.colorTag === c ? ' active' : ''}`} style={{ background: c }}
                      onClick={() => { onSetColorTag?.(widget.colorTag === c ? undefined : c); setShowMenu(false); }} />
                  ))}
                  {widget.colorTag && (
                    <button className="widget-color-clear" onClick={() => { onSetColorTag?.(undefined); setShowMenu(false); }}>Clear</button>
                  )}
                </div>
                <div className="widget-menu-divider" />
                <button className="widget-menu-item" onClick={() => { setNoteDraft(widget.note || ''); setEditingNote(true); }}>
                  {widget.note ? 'Edit Note' : 'Add Note'}
                </button>
                {editingNote && (
                  <div className="widget-menu-note-input">
                    <input value={noteDraft} onChange={e => setNoteDraft(e.target.value)} placeholder="Add a note..."
                      onKeyDown={e => { if (e.key === 'Enter') commitNote(); }} autoFocus />
                    <button onClick={commitNote}>OK</button>
                  </div>
                )}
                {otherDashboards && otherDashboards.length > 0 && (
                  <>
                    <div className="widget-menu-divider" />
                    <div className="widget-menu-label">Move to:</div>
                    {otherDashboards.map(d => (
                      <button key={d.id} className="widget-menu-item" onClick={() => { onMoveTo?.(d.id); setShowMenu(false); }}>
                        {d.name}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>

          <button className="dashboard-widget-remove" onClick={onRemove} title="Remove widget">&#x2715;</button>
        </div>
      </div>

      {/* Note display */}
      {widget.note && !widget.collapsed && (
        <div className="widget-note-display">{widget.note}</div>
      )}

      {!widget.collapsed && (
        <div className="dashboard-widget-body" ref={bodyRef}>
          <WidgetBody widget={widget} refreshTick={refreshTick} />
          {agoLabel && <div className="widget-last-updated">Updated {agoLabel}</div>}
        </div>
      )}
    </div>
  );
}

export { WidgetBody };
export default DashboardWidgetCard;

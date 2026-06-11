import type { CvRunSummary } from '../../api/cvRunsClient';

const MONTHS: Record<string, number> = {
  jan: 0,
  feb: 1,
  mar: 2,
  apr: 3,
  may: 4,
  jun: 5,
  jul: 6,
  aug: 7,
  sep: 8,
  oct: 9,
  nov: 10,
  dec: 11,
};

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const parseDateSafe = (value?: string): Date | null => {
  if (!value) return null;
  const dt = new Date(value);
  return Number.isNaN(dt.getTime()) ? null : dt;
};

const parseRunTimestamp = (runId: string): Date | null => {
  const match = String(runId || '').match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
  if (!match) return null;
  const [, y, m, d, hh, mm, ss] = match;
  const dt = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d), Number(hh), Number(mm), Number(ss)));
  return Number.isNaN(dt.getTime()) ? null : dt;
};

const inferSchemaDate = (schemaName: string): Date | null => {
  const schema = String(schemaName || '').toLowerCase();
  const yearMatch = schema.match(/(20\d{2})/);
  const monthDayMatch = schema.match(/(?:^|_)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(\d{1,2})(?:_|$)/);
  if (!yearMatch || !monthDayMatch) return null;
  const year = Number(yearMatch[1]);
  const month = MONTHS[monthDayMatch[1]];
  const day = Number(monthDayMatch[2]);
  if (!Number.isInteger(month) || !Number.isFinite(day)) return null;
  const dt = new Date(Date.UTC(year, month, day));
  return Number.isNaN(dt.getTime()) ? null : dt;
};

const parseSchemaDateRange = (schemaName: string): { start: Date | null; end: Date | null } => {
  const schema = String(schemaName || '').toLowerCase();
  const rangeMatch = schema.match(/(?:^|_)(20\d{2})(\d{2})(\d{2})_(20\d{2})(\d{2})(\d{2})(?:_|$)/);
  if (rangeMatch) {
    const [, y1, m1, d1, y2, m2, d2] = rangeMatch;
    const start = new Date(Date.UTC(Number(y1), Number(m1) - 1, Number(d1)));
    const end = new Date(Date.UTC(Number(y2), Number(m2) - 1, Number(d2)));
    return {
      start: Number.isNaN(start.getTime()) ? null : start,
      end: Number.isNaN(end.getTime()) ? null : end,
    };
  }
  const single = inferSchemaDate(schemaName);
  return { start: single, end: single };
};

const formatShortDate = (dt: Date | null): string => {
  if (!dt) return '';
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};

const resolveRunDateRange = (run: CvRunSummary): { start: Date | null; end: Date | null } => {
  const start = parseDateSafe(run.ts_start);
  const end = parseDateSafe(run.ts_end);
  if (start || end) return { start, end };
  const schemaRange = parseSchemaDateRange(run.schema_name);
  if (schemaRange.start || schemaRange.end) return schemaRange;
  const fallback = parseRunTimestamp(run.run_id) || parseDateSafe(run.created_at);
  return { start: fallback, end: fallback };
};

const formatRangeDate = (dt: Date): { year: number; month: string; day: number } => ({
  year: dt.getUTCFullYear(),
  month: MONTH_LABELS[dt.getUTCMonth()] || '',
  day: dt.getUTCDate(),
});

const formatDateRange = (start: Date | null, end: Date | null): string => {
  if (!start && !end) return '';
  if (!start || !end) return formatShortDate(start || end);

  const s = formatRangeDate(start);
  const e = formatRangeDate(end);
  if (s.year === e.year && s.month === e.month && s.day === e.day) {
    return `${s.month} ${s.day}, ${s.year}`;
  }
  if (s.year === e.year && s.month === e.month) {
    return `${s.month} ${s.day}-${e.day}, ${s.year}`;
  }
  if (s.year === e.year) {
    return `${s.month} ${s.day} - ${e.month} ${e.day}, ${s.year}`;
  }
  return `${s.month} ${s.day}, ${s.year} - ${e.month} ${e.day}, ${e.year}`;
};

const inferSeason = (run: CvRunSummary): string => {
  if (run.season && run.season.trim()) return run.season.trim();
  if (run.season_tag && run.season_tag.trim()) return run.season_tag.trim();
  const hay = `${run.run_id} ${run.schema_name} ${(run.tags || []).join(' ')}`.toLowerCase();
  if (hay.includes('winter')) return 'Winter';
  if (hay.includes('summer')) return 'Summer';
  if (hay.includes('fall') || hay.includes('autumn')) return 'Fall';
  if (hay.includes('spring')) return 'Spring';
  const { start, end } = resolveRunDateRange(run);
  const dt = end || start || parseRunTimestamp(run.run_id) || inferSchemaDate(run.schema_name);
  if (!dt) return '';
  const m = dt.getUTCMonth();
  if (m <= 1 || m === 11) return 'Winter';
  if (m <= 4) return 'Spring';
  if (m <= 7) return 'Summer';
  return 'Fall';
};

const formatPointCount = (value?: number): string => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M pts`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k pts`;
  return `${value} pts`;
};

const runSortDate = (run: CvRunSummary): number => {
  const { start, end } = resolveRunDateRange(run);
  const dt = end || start || parseDateSafe(run.created_at) || parseRunTimestamp(run.run_id) || inferSchemaDate(run.schema_name);
  return dt ? dt.getTime() : 0;
};

export const pickRunsForSelector = (runs: CvRunSummary[], activeRunId: string): CvRunSummary[] => {
  const all = Array.isArray(runs) ? runs : [];
  if (!all.length) return [];

  const hasVisibilityFlags = all.some(
    (run) => typeof run.is_visible === 'boolean' || typeof run.is_selectable === 'boolean'
  );
  const maybeVisible = hasVisibilityFlags
    ? all.filter((run) => (run.is_visible ?? run.is_selectable ?? true) === true)
    : all;
  const source = maybeVisible.length ? maybeVisible : all;

  const bySchema = new globalThis.Map<string, CvRunSummary[]>();
  source.forEach((run) => {
    const schema = String(run.schema_name || '').trim() || '__unknown__';
    const existing = bySchema.get(schema) || [];
    existing.push(run);
    bySchema.set(schema, existing);
  });

  const selected: CvRunSummary[] = [];
  bySchema.forEach((group: CvRunSummary[]) => {
    const activeMatch = group.find((run: CvRunSummary) => String(run.run_id) === activeRunId);
    if (activeMatch) {
      selected.push(activeMatch);
      return;
    }
    const sorted = [...group].sort((a, b) => {
      const t = runSortDate(b) - runSortDate(a);
      if (t !== 0) return t;
      return String(b.run_id).localeCompare(String(a.run_id));
    });
    selected.push(sorted[0]);
  });

  if (activeRunId && !selected.some((run) => String(run.run_id) === activeRunId)) {
    const activeFromAll = all.find((run) => String(run.run_id) === activeRunId);
    if (activeFromAll) selected.unshift(activeFromAll);
  }

  selected.sort((a, b) => {
    if (String(a.run_id) === activeRunId) return -1;
    if (String(b.run_id) === activeRunId) return 1;
    const t = runSortDate(b) - runSortDate(a);
    if (t !== 0) return t;
    return String(a.schema_name).localeCompare(String(b.schema_name));
  });

  return selected;
};

export const buildRunOptionLabel = (run: CvRunSummary): string => {
  const stateCode = (
    run.region ||
    run.state_code ||
    (Array.isArray(run.states) ? run.states[0] : '') ||
    ''
  ).trim();
  const region = stateCode ? stateCode.toUpperCase() : '';
  const { start, end } = resolveRunDateRange(run);
  const dateRange = formatDateRange(start, end);
  const points = formatPointCount(run.point_count);
  const preferred = [region, dateRange, points].filter(Boolean).join(' • ');
  if (preferred) return preferred;

  const display = (run.display_name || '').trim();
  if (display) {
    return display;
  }

  const season = inferSeason(run);
  const base = [season, region, points].filter(Boolean).join(' • ');
  if (base) return base;
  return String(run.schema_name || run.run_id || 'CV Run');
};

export const resolveRoadTileUrl = (baseUrl: string, datasetId?: string, mode?: 'speed' | 'vehicles') => {
  if (!baseUrl) return '';
  let resolved = baseUrl;
  if (datasetId && !resolved.includes('dataset_id=')) {
    const separator = resolved.includes('?') ? '&' : '?';
    resolved = `${resolved}${separator}dataset_id=${encodeURIComponent(datasetId)}`;
  }
  if (mode && !resolved.includes('mode=')) {
    const separator = resolved.includes('?') ? '&' : '?';
    resolved = `${resolved}${separator}mode=${encodeURIComponent(mode)}`;
  }
  return resolved;
};

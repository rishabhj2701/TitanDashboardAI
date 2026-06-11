export type DateInput = unknown;

const parseDateInput = (value: DateInput): Date | null => {
  if (value === null || typeof value === 'undefined') return null;
  let dt: Date;
  if (value instanceof Date) {
    dt = value;
  } else if (typeof value === 'string' || typeof value === 'number') {
    dt = new Date(value);
  } else {
    return null;
  }
  return Number.isNaN(dt.getTime()) ? null : dt;
};

const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
});

const TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  hour: 'numeric',
  minute: '2-digit',
});

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

const DATE_TIME_WITH_SECONDS_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
});

export const formatDateHuman = (value: DateInput, fallback = 'N/A'): string => {
  const dt = parseDateInput(value);
  return dt ? DATE_FORMATTER.format(dt) : fallback;
};

export const formatTimeHuman = (value: DateInput, fallback = 'N/A'): string => {
  const dt = parseDateInput(value);
  return dt ? TIME_FORMATTER.format(dt) : fallback;
};

export const formatDateTimeHuman = (
  value: DateInput,
  fallback = 'N/A',
  includeSeconds = false
): string => {
  const dt = parseDateInput(value);
  if (!dt) return fallback;
  return includeSeconds ? DATE_TIME_WITH_SECONDS_FORMATTER.format(dt) : DATE_TIME_FORMATTER.format(dt);
};

export const formatDateRangeHuman = (
  start: DateInput,
  end: DateInput,
  fallback = 'N/A'
): string => {
  const startLabel = formatDateTimeHuman(start, '');
  const endLabel = formatDateTimeHuman(end, '');
  if (startLabel && endLabel) return `${startLabel} to ${endLabel}`;
  if (startLabel) return startLabel;
  if (endLabel) return endLabel;
  return fallback;
};

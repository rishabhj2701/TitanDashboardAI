export interface HourlyVehicleEntry {
  hour: number;
  value: number;
}

const coerceHourlyObject = (raw: unknown): Record<string, unknown> | null => {
  let parsed: unknown = raw;
  if (typeof parsed === 'string') {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  return parsed as Record<string, unknown>;
};

export const parseHourlyUniqueVehicles = (raw: unknown): HourlyVehicleEntry[] => {
  const hourly = coerceHourlyObject(raw);
  if (!hourly) return [];

  return Object.entries(hourly)
    .map(([hourRaw, valueRaw]) => {
      const hour = Number(hourRaw);
      const value = Number(valueRaw);
      if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isFinite(value)) return null;
      return { hour, value: Math.max(0, value) };
    })
    .filter((entry): entry is HourlyVehicleEntry => Boolean(entry))
    .sort((a, b) => a.hour - b.hour);
};

export const computeAvgUniqueVehiclesPerHourFromHourlyJson = (raw: unknown): number | null => {
  const entries = parseHourlyUniqueVehicles(raw);
  if (!entries.length) return null;
  const valueByHour = new Map(entries.map((entry) => [entry.hour, entry.value]));
  let total = 0;
  for (let hour = 0; hour < 24; hour += 1) {
    total += valueByHour.get(hour) ?? 0;
  }
  return total / 24;
};

import { describe, expect, it } from 'vitest';
import {
  computeAvgUniqueVehiclesPerHourFromHourlyJson,
  parseHourlyUniqueVehicles,
} from './hourlyVehicles';

describe('hourlyVehicles utils', () => {
  it('parses hourly JSON entries and keeps valid 0-23 hours', () => {
    const entries = parseHourlyUniqueVehicles({
      '0': 2,
      '1': '3',
      '24': 99,
      bad: 4,
      '9': -1,
    });
    expect(entries).toEqual([
      { hour: 0, value: 2 },
      { hour: 1, value: 3 },
      { hour: 9, value: 0 },
    ]);
  });

  it('computes average per hour using full 24-hour denominator', () => {
    const avg = computeAvgUniqueVehiclesPerHourFromHourlyJson({
      '9': 8,
      '10': 4,
    });
    expect(avg).toBeCloseTo(0.5);
  });

  it('returns null when hourly payload is invalid or empty', () => {
    expect(computeAvgUniqueVehiclesPerHourFromHourlyJson(null)).toBeNull();
    expect(computeAvgUniqueVehiclesPerHourFromHourlyJson('{}')).toBeNull();
  });
});

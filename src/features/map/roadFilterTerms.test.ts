import { describe, expect, it } from 'vitest';

import { sanitizeRoadFilterTerm, sanitizeRoadFilterTerms } from './roadFilterTerms';

describe('roadFilterTerms', () => {
  it('strips SQL wildcard syntax from a term', () => {
    expect(sanitizeRoadFilterTerm('%I 70%')).toBe('I 70');
    expect(sanitizeRoadFilterTerm('I-70%')).toBe('I-70');
  });

  it('deduplicates cleaned terms case-insensitively', () => {
    expect(sanitizeRoadFilterTerms(['%I 70%', ' i 70 ', '"I 70"', 'I-70'])).toEqual(['I 70', 'I-70']);
  });
});

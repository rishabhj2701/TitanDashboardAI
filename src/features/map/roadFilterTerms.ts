const SURROUNDING_QUOTES_RE = /^['"]+|['"]+$/g;
const WILDCARD_RE = /[%_*]+/g;
const WHITESPACE_RE = /\s+/g;

export const sanitizeRoadFilterTerm = (term: string): string | null => {
  let cleaned = String(term ?? '').trim();
  if (!cleaned) return null;

  cleaned = cleaned.replace(SURROUNDING_QUOTES_RE, '');
  cleaned = cleaned.replace(WILDCARD_RE, ' ');
  cleaned = cleaned.replace(WHITESPACE_RE, ' ').trim();
  return cleaned || null;
};

export const sanitizeRoadFilterTerms = (terms: string[]): string[] => {
  const out: string[] = [];
  const seen = new Set<string>();

  for (const term of terms || []) {
    const cleaned = sanitizeRoadFilterTerm(String(term ?? ''));
    if (!cleaned) continue;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(cleaned);
  }
  return out;
};

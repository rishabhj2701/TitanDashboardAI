const SESSION_ID_KEY = 'traffic_session_id_v1';

const hasBrowserSession = (): boolean =>
  typeof window !== 'undefined' && typeof sessionStorage !== 'undefined';

const newSessionId = (): string => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
};

export const getSessionId = (): string => {
  if (!hasBrowserSession()) return 'main';

  const existing = sessionStorage.getItem(SESSION_ID_KEY);
  if (existing) return existing;

  const id = newSessionId();
  sessionStorage.setItem(SESSION_ID_KEY, id);
  return id;
};

export const setSessionId = (sessionId: string): string => {
  const normalized = (sessionId || '').trim();
  if (!normalized) {
    throw new Error('sessionId must be a non-empty string');
  }
  if (hasBrowserSession()) {
    sessionStorage.setItem(SESSION_ID_KEY, normalized);
  }
  return normalized;
};

export const clearSessionId = (): void => {
  if (!hasBrowserSession()) return;
  sessionStorage.removeItem(SESSION_ID_KEY);
};

export { SESSION_ID_KEY };

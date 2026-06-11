const ANON_SCOPE = 'anonymous';

const normalizeUserScope = (userId?: string | null): string => {
  const value = (userId || '').trim();
  return value || ANON_SCOPE;
};

export const userScopedStorageKey = (baseKey: string, userId?: string | null): string =>
  `${baseKey}::${normalizeUserScope(userId)}`;

export const removeScopedStorageKeys = (baseKeys: string[], userId?: string | null): void => {
  const scopedKeys = baseKeys.map((key) => userScopedStorageKey(key, userId));
  const allKeys = [...baseKeys, ...scopedKeys];

  try {
    for (const key of allKeys) {
      localStorage.removeItem(key);
    }
  } catch {
    // Ignore storage access errors.
  }

  try {
    for (const key of allKeys) {
      sessionStorage.removeItem(key);
    }
  } catch {
    // Ignore storage access errors.
  }
};

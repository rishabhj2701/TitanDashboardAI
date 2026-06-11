import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { clearSessionId } from '../api/session';
import { removeScopedStorageKeys } from '../utils/storageScope';

interface AuthUser {
  user_id: string;
  email?: string;
  name?: string;
  avatar_url?: string;
  provider?: string;
  created_at?: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  authError: string | null;
  clearAuthError: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: null,
  loading: true,
  authError: null,
  clearAuthError: () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

const ERROR_MESSAGES: Record<string, string> = {
  access_denied: 'You denied access. Please try again.',
  google_token_failed: 'Google sign-in failed. Please try again.',
  google_userinfo_failed: 'Could not retrieve your Google account info.',
  google_unexpected: 'Something went wrong with Google sign-in.',
  github_denied: 'GitHub denied access. Please try again.',
  github_token_failed: 'GitHub sign-in failed. Please try again.',
  github_user_failed: 'Could not retrieve your GitHub account info.',
  github_unexpected: 'Something went wrong with GitHub sign-in.',
  state_mismatch: 'Sign-in session expired or was invalid. Please try again.',
  missing_code: 'Sign-in was cancelled. Please try again.',
};

const CLIENT_STATE_KEYS = [
  'traffic_chat_history',
  'traffic_chat_history_v2',
  'traffic_chat_initial_load_v1',
  'active_cv_run_id',
  'traffic_app_state_v2',
];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('auth_token'));
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  const clearAuthError = useCallback(() => setAuthError(null), []);

  const logout = useCallback(() => {
    const userId = user?.user_id;
    localStorage.removeItem('auth_token');
    clearSessionId();
    removeScopedStorageKeys(CLIENT_STATE_KEYS, userId);
    setToken(null);
    setUser(null);
  }, [user?.user_id]);

  // Check for token or error in URL hash
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.startsWith('#token=')) {
      const newToken = hash.slice('#token='.length);
      localStorage.setItem('auth_token', newToken);
      setToken(newToken);
      window.history.replaceState(null, '', window.location.pathname);
    } else if (hash.startsWith('#error=')) {
      const errorCode = hash.slice('#error='.length);
      setAuthError(ERROR_MESSAGES[errorCode] || 'Sign-in failed. Please try again.');
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, []);

  // Validate token by calling /api/auth/me
  useEffect(() => {
    if (!token) {
      setLoading(false);
      setUser(null);
      return;
    }

    let mounted = true;
    const validate = async () => {
      try {
        const res = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Invalid token');
        const data = await res.json();
        if (mounted) setUser(data);
      } catch {
        localStorage.removeItem('auth_token');
        if (mounted) {
          setToken(null);
          setUser(null);
        }
      } finally {
        if (mounted) setLoading(false);
      }
    };
    void validate();
    return () => { mounted = false; };
  }, [token]);

  return (
    <AuthContext.Provider value={{ user, token, loading, authError, clearAuthError, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

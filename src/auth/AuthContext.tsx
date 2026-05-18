/**
 * AuthContext — NO-AUTH DEV MODE
 *
 * Auth is disabled for local deployment.
 * A static "dev-user" guest is injected automatically on load.
 * Login/logout/OAuth flows are all no-ops.
 */
import { createContext, useContext, type ReactNode } from 'react';

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

// Static dev user — no authentication required
const DEV_USER: AuthUser = {
  user_id: 'dev-user',
  email: 'dev@localhost',
  name: 'Dev User',
  provider: 'local',
  created_at: new Date().toISOString(),
};

const AuthContext = createContext<AuthContextValue>({
  user: DEV_USER,
  token: 'dev-token',
  loading: false,
  authError: null,
  clearAuthError: () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <AuthContext.Provider
      value={{
        user: DEV_USER,
        token: 'dev-token',
        loading: false,
        authError: null,
        clearAuthError: () => {},
        logout: () => {},
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

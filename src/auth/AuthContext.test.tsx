import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthProvider, useAuth } from './AuthContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

describe('AuthContext dev mode', () => {
  it('provides a static dev user without loading', () => {
    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.loading).toBe(false);
    expect(result.current.user?.user_id).toBe('dev-user');
    expect(result.current.token).toBe('dev-token');
    expect(result.current.authError).toBeNull();
  });

  it('logout is a no-op and does not clear persisted storage', () => {
    localStorage.setItem('auth_token', 'token-a');
    localStorage.setItem('titan_dashboards_v3::alice', '{"dashboards":[]}');
    sessionStorage.setItem('traffic_session_id_v1', 'session-a');
    sessionStorage.setItem('traffic_chat_history_v2::alice', '[{"role":"user","content":"a"}]');

    const { result } = renderHook(() => useAuth(), { wrapper });

    act(() => {
      result.current.logout();
    });

    expect(result.current.user?.user_id).toBe('dev-user');
    expect(result.current.token).toBe('dev-token');
    expect(localStorage.getItem('auth_token')).toBe('token-a');
    expect(localStorage.getItem('titan_dashboards_v3::alice')).not.toBeNull();
    expect(sessionStorage.getItem('traffic_session_id_v1')).toBe('session-a');
    expect(sessionStorage.getItem('traffic_chat_history_v2::alice')).not.toBeNull();
  });
});

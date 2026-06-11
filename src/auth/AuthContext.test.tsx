import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

describe('AuthContext logout cleanup', () => {
  it('clears token/session/chat but preserves dashboard keys for returning users', async () => {
    localStorage.setItem('auth_token', 'token-a');
    localStorage.setItem('titan_dashboards_v3::alice', '{"dashboards":[]}');
    localStorage.setItem('titan_dashboards_v3::bob', '{"dashboards":[]}');
    sessionStorage.setItem('traffic_session_id_v1', 'session-a');
    sessionStorage.setItem('traffic_chat_history_v2::alice', '[{"role":"user","content":"a"}]');
    sessionStorage.setItem('traffic_chat_history_v2::bob', '[{"role":"user","content":"b"}]');

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ user_id: 'alice', email: 'alice@example.com' }),
      })
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.user?.user_id).toBe('alice');
    });

    act(() => {
      result.current.logout();
    });

    expect(localStorage.getItem('auth_token')).toBeNull();
    expect(sessionStorage.getItem('traffic_session_id_v1')).toBeNull();
    expect(localStorage.getItem('titan_dashboards_v3::alice')).not.toBeNull();
    expect(sessionStorage.getItem('traffic_chat_history_v2::alice')).toBeNull();

    expect(localStorage.getItem('titan_dashboards_v3::bob')).not.toBeNull();
    expect(sessionStorage.getItem('traffic_chat_history_v2::bob')).not.toBeNull();
  });
});

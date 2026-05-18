import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useChatHistory } from './useChatHistory';

describe('useChatHistory storage key isolation', () => {
  it('loads messages from the provided storage key', async () => {
    const aliceKey = 'traffic_chat_history_v2::alice';
    const bobKey = 'traffic_chat_history_v2::bob';
    sessionStorage.setItem(aliceKey, JSON.stringify([{ role: 'user', content: 'alice message' }]));
    sessionStorage.setItem(bobKey, JSON.stringify([{ role: 'user', content: 'bob message' }]));

    const { result } = renderHook(
      ({ storageKey }) => useChatHistory({ storageKey }),
      { initialProps: { storageKey: aliceKey } }
    );

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(1);
    });
    expect(result.current.messages[0].content).toBe('alice message');
  });

  it('writes updates to only the active storage key', async () => {
    const aliceKey = 'traffic_chat_history_v2::alice';
    const bobKey = 'traffic_chat_history_v2::bob';
    sessionStorage.setItem(bobKey, JSON.stringify([{ role: 'assistant', content: 'keep bob' }]));

    const { result } = renderHook(
      ({ storageKey }) => useChatHistory({ storageKey }),
      { initialProps: { storageKey: aliceKey } }
    );

    act(() => {
      result.current.setMessages([{ role: 'assistant', content: 'alice only' }]);
    });

    await waitFor(() => {
      const saved = sessionStorage.getItem(aliceKey) || '';
      expect(saved).toContain('alice only');
    });

    const bobSaved = sessionStorage.getItem(bobKey) || '';
    expect(bobSaved).toContain('keep bob');
    expect(bobSaved).not.toContain('alice only');
  });
});

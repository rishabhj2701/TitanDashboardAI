import { apiFetchApp } from './http';
import { getSessionId } from './session';

export interface ChatRequestPayload {
  message: string;
  sessionId?: string;
  history?: unknown[];
  fileData?: unknown;
}

export interface ChatResponsePayload {
  sessionId?: string;
  response?: string;
  responseText?: string;
  mapSelection?: {
    points?: unknown[];
    lines?: unknown[];
    overlay?: boolean;
    roadAggregateFilter?: Record<string, unknown>;
  };
  chartPayload?: unknown;
  graphData?: unknown;
}

export interface ChatClearRequestPayload {
  sessionId: string;
  clearData?: boolean;
}

export interface ChatClearResponsePayload {
  status?: string;
  sessionId?: string;
  cleared?: unknown;
  dataCleared?: boolean;
}

export const sendChat = async (
  payload: ChatRequestPayload
): Promise<ChatResponsePayload> => {
  const body = { ...payload, sessionId: payload.sessionId || getSessionId() };
  const response = await apiFetchApp('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json();
};

export const clearChat = async (
  payload: ChatClearRequestPayload
): Promise<ChatClearResponsePayload> => {
  const response = await apiFetchApp('/api/chat/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return response.json();
};

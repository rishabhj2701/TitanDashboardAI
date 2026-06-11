import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import type { ChatMessage, ExternalChatMessage } from '../features/chat/types';

type ChatHistoryThread = {
  sessionId: string;
  title: string;
  messageCount?: number;
};

type UseChatHistoryParams = {
  storageKey: string;
  externalMessage?: ExternalChatMessage | null;
};

type UseChatHistoryResult = {
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  threads: ChatHistoryThread[];
  historyEnabled: boolean;
  refreshThreads: () => Promise<void>;
  loadSessionMessages: (sessionId: string) => Promise<ChatMessage[]>;
};

export const useChatHistory = ({ storageKey, externalMessage }: UseChatHistoryParams): UseChatHistoryResult => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threads, setThreads] = useState<ChatHistoryThread[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const lastExternalMessageIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!externalMessage) return;
    if (externalMessage.id === lastExternalMessageIdRef.current) return;
    lastExternalMessageIdRef.current = externalMessage.id;
    setMessages((prev) => [...prev, { role: externalMessage.role, content: externalMessage.content }]);
  }, [externalMessage]);

  useEffect(() => {
    sessionStorage.removeItem('traffic_chat_history');
    sessionStorage.removeItem('traffic_chat_history_v2');
    localStorage.removeItem('traffic_chat_history');
    localStorage.removeItem('traffic_chat_history_v2');

    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved) {
        const history = JSON.parse(saved) as ChatMessage[];
        setMessages(history);
      }
    } catch (error) {
      console.error('Failed to load chat history:', error);
      setMessages([]);
    }
  }, [storageKey]);

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(messages));
    } catch (error) {
      console.error('Failed to save chat history:', error);
    }
  }, [messages, storageKey]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const refreshThreads = useCallback(async () => {
    setThreads([]);
  }, []);

  const loadSessionMessages = useCallback(async (sessionId: string): Promise<ChatMessage[]> => {
    void sessionId;
    return [];
  }, []);

  return {
    messages,
    setMessages,
    messagesEndRef,
    threads,
    historyEnabled: false,
    refreshThreads,
    loadSessionMessages,
  };
};

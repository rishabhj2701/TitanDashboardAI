import type { ReactNode } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { renderRichMarkdown } from './markdown';
import { sendChat } from '../../api/chatClient';
import type { ChatMessage, UploadedFileData } from './types';

type RenderMessageContentParams = {
  onOpenIngestion?: () => void;
  sessionIdRef: React.RefObject<string>;
  messages: ChatMessage[];
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setIsLoading: Dispatch<SetStateAction<boolean>>;
  uploadedFileData: UploadedFileData | null;
  onChartPayload?: (payloads: unknown[]) => void;
};

export const createMessageRenderer = ({
  onOpenIngestion,
  sessionIdRef,
  messages,
  setMessages,
  setIsLoading,
  uploadedFileData,
  onChartPayload,
}: RenderMessageContentParams): ((content: string) => ReactNode) => {
  return (content: string): ReactNode => {
    const ingestionMarker = '[[OPEN_INGESTION]]';
    if (content.includes(ingestionMarker)) {
      const parts = content.split(ingestionMarker);
      return (
        <>
          {renderRichMarkdown(parts[0])}
          <button
            onClick={() => onOpenIngestion?.()}
            style={{
              background: 'rgba(16, 185, 129, 0.2)',
              border: '1px solid rgba(16, 185, 129, 0.5)',
              borderRadius: '6px',
              padding: '4px 10px',
              margin: '0 6px',
              cursor: 'pointer',
              fontSize: '0.85em',
              color: '#6ee7b7',
              fontWeight: '600',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(16, 185, 129, 0.3)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(16, 185, 129, 0.2)';
            }}
          >
            Open Ingestion
          </button>
          {parts[1] ? renderRichMarkdown(parts[1]) : ''}
        </>
      );
    }

    const morePattern = /\.\.\.and (\d+) more results available\./;
    const match = content.match(morePattern);

    if (match) {
      const moreCount = match[1];
      const parts = content.split(morePattern);

      return (
        <>
          {renderRichMarkdown(parts[0])}
          <button
            onClick={async () => {
              const queryMessage = `Show me all the results from the previous query`;
              setMessages(prev => [...prev, { role: 'user', content: queryMessage }]);
              setIsLoading(true);

              try {
                const data = await sendChat({
                  message: queryMessage,
                  sessionId: sessionIdRef.current,
                  history: messages,
                  fileData: uploadedFileData
                });
                const assistantReply = data.responseText || data.response || 'No response.';
                setMessages(prev => [...prev, { role: 'assistant', content: assistantReply }]);

                if (data.chartPayload && Array.isArray(data.chartPayload) && data.chartPayload.length > 0) {
                  onChartPayload?.(data.chartPayload);
                }
              } catch (error: any) {
                setMessages(prev => [...prev, {
                  role: 'assistant',
                  content: `Error: ${error.message}`
                }]);
              } finally {
                setIsLoading(false);
              }
            }}
            style={{
              background: 'rgba(99, 102, 241, 0.2)',
              border: '1px solid rgba(99, 102, 241, 0.5)',
              borderRadius: '4px',
              padding: '4px 8px',
              margin: '0 4px',
              cursor: 'pointer',
              fontSize: '0.9em',
              color: '#818cf8',
              fontWeight: '500',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(99, 102, 241, 0.3)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(99, 102, 241, 0.2)';
            }}
          >
            Show {moreCount} more
          </button>
          {parts[2] ? renderRichMarkdown(parts[2]) : ''}
        </>
      );
    }

    return <>{renderRichMarkdown(content)}</>;
  };
};

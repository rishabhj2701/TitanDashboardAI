import { useState, useRef, useEffect, useMemo } from 'react';
import './MinimalChat.css';
import type { ProcessedVehicleData } from '../services/dataLoader';
import { clearSessionId, getSessionId } from '../api/session';
import { useChatHistory } from '../hooks/useChatHistory';
import { useFileUpload } from '../hooks/useFileUpload';
import { useChatSend } from '../hooks/useChatSend';
import { createMessageRenderer } from '../features/chat/messageRenderers';
import ChatMessagesPanel from './chat/ChatMessagesPanel';
import ChatInputArea from './chat/ChatInputArea';
import type { ExternalChatMessage, WorkzoneLinePayload } from '../features/chat/types';
import { userScopedStorageKey } from '../utils/storageScope';

interface MinimalChatProps {
  userId?: string;
  onFilter: (filterType: string, value: unknown) => void;
  onVisualize: (type: string) => void;
  vehicleData: ProcessedVehicleData[];
  onShowChart: (type: 'speed' | 'road' | 'violations') => void;
  onChartPayload?: (payloads: unknown[]) => void;
  onServerSelection?: (points: ProcessedVehicleData[], overlay?: boolean) => void;
  onWorkzoneLines?: (lines: WorkzoneLinePayload[]) => void;
  onRoadAggregateFilter?: (filter: { road_name?: string; road_segment_id?: string; road_names?: string[]; min_points?: number; limit?: number }) => void;
  onOpenIngestion?: () => void;
  onClearHistory?: () => void;
  externalMessage?: ExternalChatMessage | null;
}

const CHAT_STORAGE_BASE_KEY = 'traffic_chat_history_v2';
const INITIAL_LOAD_BASE_KEY = 'traffic_chat_initial_load_v1';

const MinimalChat: React.FC<MinimalChatProps> = ({
  userId,
  onVisualize,
  onShowChart,
  onChartPayload,
  onServerSelection,
  onWorkzoneLines,
  onRoadAggregateFilter,
  onOpenIngestion,
  onClearHistory,
  externalMessage
}) => {
  const sessionIdRef = useRef<string>('');
  if (!sessionIdRef.current) {
    sessionIdRef.current = getSessionId();
  }

  const chatStorageKey = useMemo(
    () => userScopedStorageKey(CHAT_STORAGE_BASE_KEY, userId),
    [userId]
  );
  const initialLoadKey = useMemo(
    () => userScopedStorageKey(INITIAL_LOAD_BASE_KEY, userId),
    [userId]
  );

  const [isExpanded, setIsExpanded] = useState(() => {
    const hasSeenChat = sessionStorage.getItem(initialLoadKey);
    return !hasSeenChat;
  });
  const [chatWidth, setChatWidth] = useState(380);
  const [chatHeight, setChatHeight] = useState(500);
  const resizeRef = useRef<{ startX: number; startY: number; startW: number; startH: number } | null>(null);

  const [message, setMessage] = useState('');
  const {
    messages,
    setMessages,
    messagesEndRef,
  } = useChatHistory({
    storageKey: chatStorageKey,
    externalMessage,
  });
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (isExpanded) {
      sessionStorage.setItem(initialLoadKey, 'true');
    }
  }, [isExpanded, initialLoadKey]);

  const fileUpload = useFileUpload({ setMessages, setIsLoading });

  const { handleSend } = useChatSend({
    messages,
    setMessages,
    setIsLoading,
    uploadedFileData: fileUpload.uploadedFileData,
    onVisualize,
    onShowChart,
    onChartPayload,
    onServerSelection,
    onWorkzoneLines,
    onRoadAggregateFilter,
    onClearHistory,
    resetUploadState: fileUpload.resetUploadState,
    chatStorageKey,
  });

  const renderMessageContent = useMemo(() => createMessageRenderer({
    onOpenIngestion,
    sessionIdRef,
    messages,
    setMessages,
    setIsLoading,
    uploadedFileData: fileUpload.uploadedFileData,
    onChartPayload,
  }), [onOpenIngestion, messages, fileUpload.uploadedFileData, onChartPayload]);

  const onSend = () => {
    if (!message.trim() || isLoading) return;
    const userMessage = message.trim();
    setMessage('');
    handleSend(userMessage);
  };

  const handleNewSession = async () => {
    clearSessionId();
    const fresh = getSessionId();
    sessionIdRef.current = fresh;
    setMessages([]);
    sessionStorage.removeItem(chatStorageKey);
  };

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: chatWidth, startH: chatHeight };
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return;
      const dw = resizeRef.current.startX - ev.clientX;
      const dh = resizeRef.current.startY - ev.clientY;
      setChatWidth(Math.max(320, Math.min(700, resizeRef.current.startW + dw)));
      setChatHeight(Math.max(400, Math.min(800, resizeRef.current.startH + dh)));
    };
    const onUp = () => {
      resizeRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  const handleClearChat = () => {
    if (confirm('Clear all chat messages?')) {
      setMessages([]);
      sessionStorage.removeItem(chatStorageKey);
    }
  };

  const handleExportChat = () => {
    const text = messages.map(m => `[${m.role}] ${m.content}`).join('\n\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = 'chat_export.txt';
    link.href = url;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className={`minimal-chat ${isExpanded ? 'expanded' : ''}`}

    >
      {!isExpanded ? (
        <div
          className="chat-trigger"
          onClick={() => setIsExpanded(true)}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </div>
      ) : (
        <div className="chat-expanded" style={{ width: chatWidth, height: chatHeight }}>
          <div className="chat-resize-handle" onMouseDown={handleResizeStart} title="Drag to resize" />
          <div className="chat-header">
            <span>DOT Data Analyst</span>
            <div className="chat-header-actions">
              <button className="chat-header-btn" onClick={handleNewSession} title="New thread">&#x2795;</button>
              <button className="chat-header-btn" onClick={handleExportChat} title="Export chat">&#x2913;</button>
              <button className="chat-header-btn" onClick={handleClearChat} title="Clear chat">&#x1D5E5;</button>
              <button
                className="chat-close-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsExpanded(false);
                }}
                title="Close chat"
              >
                &#x2715;
              </button>
            </div>
          </div>
          <ChatMessagesPanel
            messages={messages}
            isLoading={isLoading}
            messagesEndRef={messagesEndRef}
            renderMessageContent={renderMessageContent}
            onPinChart={onChartPayload ? (chart) => onChartPayload([chart]) : undefined}
          />

          <ChatInputArea
            isDragging={fileUpload.isDragging}
            showUploadMenu={fileUpload.showUploadMenu}
            uploadMode={fileUpload.uploadMode}
            uploadUrl={fileUpload.uploadUrl}
            uploadStatus={fileUpload.uploadStatus}
            uploadError={fileUpload.uploadError}
            uploadInProgress={fileUpload.uploadInProgress}
            message={message}
            isLoading={isLoading}
            fileInputRef={fileUpload.fileInputRef}
            onCloseUploadMenu={() => fileUpload.setShowUploadMenu(false)}
            onToggleUploadMenu={() => fileUpload.setShowUploadMenu((prev) => !prev)}
            onSetUploadMode={fileUpload.setUploadMode}
            onUploadUrlChange={fileUpload.setUploadUrl}
            onUrlUpload={fileUpload.handleUrlUpload}
            onFilesSelected={(fileArray) => {
              if (fileArray.length === 1) {
                fileUpload.handleFileSelection(fileArray[0]);
              } else if (fileArray.length > 1) {
                fileUpload.handleMultipleFiles(fileArray);
              }
            }}
            onMessageChange={setMessage}
            onSend={onSend}
          />
        </div>
      )}
    </div>
  );
};

export default MinimalChat;

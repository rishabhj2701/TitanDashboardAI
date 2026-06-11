import { createPortal } from 'react-dom';
import type { ChangeEvent, RefObject } from 'react';

type ChatInputAreaProps = {
  isDragging: boolean;
  showUploadMenu: boolean;
  uploadMode: 'file' | 'url';
  uploadUrl: string;
  uploadStatus: string | null;
  uploadError: string | null;
  uploadInProgress: boolean;
  message: string;
  isLoading: boolean;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onCloseUploadMenu: () => void;
  onToggleUploadMenu: () => void;
  onSetUploadMode: (mode: 'file' | 'url') => void;
  onUploadUrlChange: (value: string) => void;
  onUrlUpload: () => void;
  onFilesSelected: (files: File[]) => void;
  onMessageChange: (value: string) => void;
  onSend: () => void;
};

function ChatInputArea({
  isDragging,
  showUploadMenu,
  uploadMode,
  uploadUrl,
  uploadStatus,
  uploadError,
  uploadInProgress,
  message,
  isLoading,
  fileInputRef,
  onCloseUploadMenu,
  onToggleUploadMenu,
  onSetUploadMode,
  onUploadUrlChange,
  onUrlUpload,
  onFilesSelected,
  onMessageChange,
  onSend,
}: ChatInputAreaProps) {
  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!event.target.files || event.target.files.length === 0) return;
    onFilesSelected(Array.from(event.target.files));
    event.target.value = '';
  };

  return (
    <div className="chat-input-container">
      {isDragging && typeof document !== 'undefined'
        ? createPortal(
            <div className="chat-drop-overlay">
              <div className="chat-drop-panel">
                <div className="chat-drop-title">Drop file to upload</div>
                <div className="chat-drop-subtitle">CSV, XLSX, or GeoJSON</div>
              </div>
            </div>,
            document.body
          )
        : null}
      {showUploadMenu && typeof document !== 'undefined'
        ? createPortal(
            <div className="chat-upload-portal" onClick={onCloseUploadMenu}>
              <div className="chat-upload-backdrop" />
              <div className="chat-upload-menu" onClick={(event) => event.stopPropagation()}>
                <div className="chat-upload-tabs">
                  <button
                    className={`chat-upload-tab ${uploadMode === 'file' ? 'active' : ''}`}
                    onClick={() => onSetUploadMode('file')}
                  >
                    Upload File
                  </button>
                  <button
                    className={`chat-upload-tab ${uploadMode === 'url' ? 'active' : ''}`}
                    onClick={() => onSetUploadMode('url')}
                  >
                    Upload via URL
                  </button>
                </div>
                {uploadMode === 'file' ? (
                  <div className="chat-upload-row">
                    <button
                      className="chat-upload-action"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploadInProgress}
                    >
                      Choose file
                    </button>
                    <span className="chat-upload-hint">CSV or GeoJSON</span>
                  </div>
                ) : (
                  <div className="chat-upload-row">
                    <input
                      className="chat-upload-input"
                      type="text"
                      placeholder="Paste JSON/GeoJSON URL"
                      value={uploadUrl}
                      onChange={(event) => onUploadUrlChange(event.target.value)}
                      disabled={uploadInProgress}
                    />
                    <button
                      className="chat-upload-action"
                      onClick={onUrlUpload}
                      disabled={uploadInProgress}
                    >
                      Load
                    </button>
                  </div>
                )}
                {uploadStatus ? <div className="chat-upload-status">{uploadStatus}</div> : null}
                {uploadError ? <div className="chat-upload-error">{uploadError}</div> : null}
              </div>
            </div>,
            document.body
          )
        : null}
      <input
        type="file"
        ref={fileInputRef}
        multiple
        style={{
          position: 'absolute',
          width: '1px',
          height: '1px',
          padding: 0,
          margin: '-1px',
          overflow: 'hidden',
          clip: 'rect(0,0,0,0)',
          border: 0,
        }}
        accept=".csv,.xlsx,.xls,.json,.geojson,.txt"
        onChange={handleFileChange}
      />
      <button
        onClick={onToggleUploadMenu}
        className="chat-upload-btn"
        title="Upload file or URL"
      >
        📎
      </button>
      <input
        type="text"
        value={message}
        onChange={(event) => onMessageChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onSend();
        }}
        placeholder="Ask me anything about the data..."
        className="chat-input"
      />
      <button onClick={onSend} className="chat-send-btn" disabled={isLoading}>
        ➤
      </button>
    </div>
  );
}

export default ChatInputArea;

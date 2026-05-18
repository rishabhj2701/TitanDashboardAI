import { useCallback, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import ChartRenderer from '../ChartRenderer';
import type { GeneratedChartPayload } from '../../types/charts';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  charts?: GeneratedChartPayload[];
};

type ChatMessagesPanelProps = {
  messages: ChatMessage[];
  isLoading: boolean;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  renderMessageContent: (content: string) => ReactNode;
  onPinChart?: (chart: GeneratedChartPayload) => void;
};

function InlineChart({ chart, onPin }: { chart: GeneratedChartPayload; onPin?: (c: GeneratedChartPayload) => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`chat-inline-chart${expanded ? ' expanded' : ''}`}>
      <div className="chat-inline-chart-canvas">
        <ChartRenderer payload={chart} title={chart.title || ''} variant="preview" />
      </div>
      <div className="chat-chart-actions">
        <button className="chat-chart-action-btn" onClick={() => setExpanded(!expanded)}>
          {expanded ? '\u2715 Collapse' : '\u26F6 Expand'}
        </button>
        {onPin && (
          <button className="chat-chart-action-btn pin" onClick={() => onPin(chart)}>
            {'\u{1F4CC}'} Pin to Dashboard
          </button>
        )}
      </div>
    </div>
  );
}

function ChatMessagesPanel({ messages, isLoading, messagesEndRef, renderMessageContent, onPinChart }: ChatMessagesPanelProps) {
  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
  }, []);

  return (
    <div className="chat-messages">
      {messages.length === 0 && (
        <div className="chat-welcome">
          <div className="chat-welcome-title">DOT Data Analyst</div>
          <div className="chat-welcome-subtitle">
            Ask about Iowa crash records, CV segment speeds, or both — use &quot;show&quot; / &quot;map&quot; to plot on the map.
          </div>
          <div className="chat-welcome-prompts">
            <span className="chat-welcome-chip">Map fatal crashes in Polk County</span>
            <span className="chat-welcome-chip">Top 10 routes by crash count</span>
            <span className="chat-welcome-chip">CV roads with most hard braking</span>
            <span className="chat-welcome-chip">Crash count vs avg CV speed by route</span>
          </div>
        </div>
      )}
      {messages.map((msg, idx) => (
        <div key={idx} className={`chat-message ${msg.role}`}>
          <button className="chat-msg-copy" onClick={() => handleCopy(msg.content)} title="Copy message">&#x2398;</button>
          {renderMessageContent(msg.content)}
          {/* Inline charts */}
          {msg.charts && msg.charts.length > 0 && (
            <div className="chat-inline-charts">
              {msg.charts.map((chart, cIdx) => (
                <InlineChart key={cIdx} chart={chart as GeneratedChartPayload} onPin={onPinChart} />
              ))}
            </div>
          )}
        </div>
      ))}
      {isLoading && (
        <div className="chat-message assistant loading">
          <div className="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </div>
      )}
      <div ref={messagesEndRef} />
    </div>
  );
}

export default ChatMessagesPanel;

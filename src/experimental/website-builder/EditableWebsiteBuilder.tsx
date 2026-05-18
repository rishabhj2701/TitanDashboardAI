// @ts-nocheck
import React, { useState, useRef, useEffect } from 'react';
import { createEditableWebsite, editWebsite } from './api/websiteClient';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

interface Action {
  type: string;
  description: string;
  timestamp: number;
}

interface SiteState {
  site_id: string;
  name: string;
  description: string;
  version: number;
  header?: any;
  navigation?: any;
  sections: any[];
  charts: any[];
  tables: any[];
  footer?: any;
  theme: any;
  action_history: Action[];
}

export const EditableWebsiteBuilder: React.FC = () => {
  const [step, setStep] = useState<'upload' | 'create' | 'edit'>('upload');
  const [uploadedData, setUploadedData] = useState<any>(null);
  const [userRequest, setUserRequest] = useState('');
  const [siteState, setSiteState] = useState<SiteState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [editInstruction, setEditInstruction] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll chat to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [editInstruction, userRequest]);

  // Handle file upload
  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const lines = text.split('\n');
      const headers = lines[0].split(',').map(h => h.trim());

      const data = lines.slice(1, 51).map(line => {
        const values = line.split(',');
        const row: any = {};
        headers.forEach((header, i) => {
          row[header] = values[i]?.trim() || '';
        });
        return row;
      }).filter(row => Object.keys(row).length > 0);

      setUploadedData({
        columns: headers,
        data: data,
        stats: {
          row_count: data.length,
          column_count: headers.length
        }
      });

      setMessages([{
        role: 'system',
        content: `✅ Uploaded **${file.name}**\n📊 ${data.length} rows × ${headers.length} columns\n\n**Columns:** ${headers.join(', ')}`,
        timestamp: Date.now()
      }]);

      setStep('create');
    };
    reader.readAsText(file);
  };

  // Create initial website
  const handleCreateWebsite = async () => {
    if (!uploadedData || !userRequest.trim()) return;

    setIsLoading(true);
    setMessages(prev => [...prev, {
      role: 'user',
      content: userRequest,
      timestamp: Date.now()
    }]);

    try {
      const result = await createEditableWebsite({
        request: userRequest,
        data_columns: uploadedData.columns,
        sample_data: uploadedData.data,
        dataset_stats: uploadedData.stats
      }) as any;
      setSiteState(result.site_state);

      // Save to sessionStorage
      const sitesDataStr = sessionStorage.getItem('generated_sites_data_v2');
      const sitesData = sitesDataStr ? JSON.parse(sitesDataStr) : {};
      sitesData[result.site_state.site_id] = result.site_state;
      sessionStorage.setItem('generated_sites_data_v2', JSON.stringify(sitesData));

      console.log('✅ Saved to sessionStorage:', result.site_state.site_id);
      console.log('📦 All sites in storage:', Object.keys(sitesData));
      console.log('🔍 Verify save:', !!sessionStorage.getItem('generated_sites_data_v2'));

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `✅ **Website Created!**\n\n**${result.site_state.name}**\n${result.site_state.description}\n\n📊 **Components:**\n• ${result.site_state.sections?.length || 0} sections\n• ${result.site_state.charts?.length || 0} charts\n• ${result.site_state.tables?.length || 0} tables\n\n🔗 [**View Website →**](http://localhost:5173/sites/${result.site_state.site_id})\n\nI'm ready to help you edit! Try:\n• "Add a dark header"\n• "Change footer to Copyright 2025"\n• "Move chart to top"`,
        timestamp: Date.now()
      }]);

      setStep('edit');
      setUserRequest('');
    } catch (error) {
      console.error('Failed to create website:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `❌ Sorry, I encountered an error: ${error instanceof Error ? error.message : 'Failed to create website'}\n\nPlease try again or check the backend logs.`,
        timestamp: Date.now()
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  // Edit existing website
  const handleEditWebsite = async () => {
    if (!siteState || !editInstruction.trim()) return;

    setIsLoading(true);
    setMessages(prev => [...prev, {
      role: 'user',
      content: editInstruction,
      timestamp: Date.now()
    }]);

    try {
      const result = await editWebsite({
        site_id: siteState.site_id,
        instruction: editInstruction
      }) as any;
      setSiteState(result.site_state);

      // Update sessionStorage
      const sitesDataStr = sessionStorage.getItem('generated_sites_data_v2');
      const sitesData = sitesDataStr ? JSON.parse(sitesDataStr) : {};
      sitesData[result.site_state.site_id] = result.site_state;
      sessionStorage.setItem('generated_sites_data_v2', JSON.stringify(sitesData));

      const actionsList = result.actions.map((a: any) => `✓ ${a.description || a}`).join('\n');
      const websiteLink = result.website_url || `/sites/${result.site_state.site_id}`;
      const cacheBustedLink = `${websiteLink}?v=${result.site_state.version}&t=${Date.now()}`;

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `✅ **${result.message}**\n\n${actionsList}\n\n📍 Version ${result.site_state.version}\n\n🔗 View Updated Website: ${window.location.origin}${cacheBustedLink}`,
        timestamp: Date.now()
      }]);

      setEditInstruction('');
    } catch (error) {
      console.error('Failed to edit website:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `❌ Sorry, I couldn't apply that change: ${error instanceof Error ? error.message : 'Unknown error'}\n\nTry rephrasing your request or be more specific.`,
        timestamp: Date.now()
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (step === 'create') {
        handleCreateWebsite();
      } else if (step === 'edit') {
        handleEditWebsite();
      }
    }
  };

  // Render upload step
  if (step === 'upload') {
    return (
      <div style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        padding: '40px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginTop: '60px'
      }}>
        <div style={{
          maxWidth: '600px',
          width: '100%',
          background: 'white',
          borderRadius: '16px',
          padding: '48px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)'
        }}>
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>✨</div>
            <h1 style={{
              fontSize: '32px',
              fontWeight: 'bold',
              marginBottom: '12px',
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent'
            }}>
              AI Website Builder
            </h1>
            <p style={{
              fontSize: '16px',
              color: '#666',
              lineHeight: '1.6'
            }}>
              Build beautiful data dashboards with natural language
            </p>
          </div>

          <div style={{
            border: '2px dashed #667eea',
            borderRadius: '12px',
            padding: '48px 24px',
            textAlign: 'center',
            background: '#f7f9fc',
            marginBottom: '24px',
            cursor: 'pointer',
            transition: 'all 0.3s'
          }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const file = e.dataTransfer.files[0];
            if (file) {
              const input = document.getElementById('file-upload') as HTMLInputElement;
              const dt = new DataTransfer();
              dt.items.add(file);
              input.files = dt.files;
              input.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }}>
            <input
              type="file"
              accept=".csv"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
              id="file-upload"
            />
            <label htmlFor="file-upload" style={{ cursor: 'pointer', display: 'block' }}>
              <div style={{ fontSize: '48px', marginBottom: '16px' }}>📁</div>
              <div style={{
                fontSize: '16px',
                fontWeight: 600,
                color: '#667eea',
                marginBottom: '8px'
              }}>
                Drop your CSV file here or click to browse
              </div>
              <div style={{ fontSize: '14px', color: '#999' }}>
                Supports CSV files up to 10MB
              </div>
            </label>
          </div>

          {messages.length > 0 && (
            <div style={{
              background: '#f0fdf4',
              border: '1px solid #86efac',
              borderRadius: '8px',
              padding: '16px',
              fontSize: '14px',
              color: '#166534',
              whiteSpace: 'pre-line',
              lineHeight: '1.6'
            }}>
              {messages[messages.length - 1].content}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Render create/edit step (unified chat interface)
  return (
    <div style={{
      position: 'fixed',
      top: '60px',
      left: 0,
      right: 0,
      bottom: 0,
      display: 'flex',
      flexDirection: 'column',
      background: '#f5f7fa'
    }}>
      {/* Header */}
      {siteState && (
        <div style={{
          background: 'white',
          borderBottom: '1px solid #e0e0e0',
          padding: '16px 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0
        }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontSize: '18px', fontWeight: 600, margin: 0, color: '#333' }}>
              {siteState.name}
            </h2>
            <p style={{ fontSize: '13px', color: '#999', margin: '4px 0 0 0' }}>
              v{siteState.version} • {siteState.charts?.length || 0} charts, {siteState.sections?.length || 0} sections
            </p>
          </div>
          <a
            href={`/sites/${siteState.site_id}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              padding: '10px 20px',
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              color: 'white',
              borderRadius: '8px',
              textDecoration: 'none',
              fontSize: '14px',
              fontWeight: 600,
              display: 'inline-block'
            }}
          >
            🚀 View Website
          </a>
        </div>
      )}

      {/* Chat Messages */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '24px',
        maxWidth: '900px',
        width: '100%',
        margin: '0 auto'
      }}>
        {messages.map((msg, idx) => (
          <div
            key={idx}
            style={{
              marginBottom: '24px',
              display: 'flex',
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start'
            }}
          >
            <div style={{
              maxWidth: '80%',
              display: 'flex',
              flexDirection: 'column',
              alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start'
            }}>
              <div style={{
                fontSize: '12px',
                color: '#999',
                marginBottom: '6px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px'
              }}>
                {msg.role === 'user' ? '👤 You' : msg.role === 'assistant' ? '🤖 AI Assistant' : 'ℹ️ System'}
                <span>•</span>
                <span>{new Date(msg.timestamp).toLocaleTimeString()}</span>
              </div>
              <div style={{
                background: msg.role === 'user' ? '#667eea' : msg.role === 'assistant' ? 'white' : '#fff7ed',
                color: msg.role === 'user' ? 'white' : '#333',
                padding: '14px 18px',
                borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
                fontSize: '15px',
                lineHeight: '1.6',
                whiteSpace: 'pre-line',
                boxShadow: msg.role === 'assistant' ? '0 2px 8px rgba(0,0,0,0.1)' : 'none',
                border: msg.role === 'system' ? '1px solid #fed7aa' : 'none'
              }}>
                {msg.content}
              </div>
            </div>
          </div>
        ))}
        {isLoading && (
          <div style={{
            marginBottom: '24px',
            display: 'flex',
            justifyContent: 'flex-start'
          }}>
            <div style={{
              background: 'white',
              padding: '14px 18px',
              borderRadius: '18px 18px 18px 4px',
              boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
            }}>
              <div style={{ display: 'flex', gap: '6px' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#667eea', animation: 'bounce 1.4s infinite ease-in-out both', animationDelay: '-0.32s' }}></div>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#667eea', animation: 'bounce 1.4s infinite ease-in-out both', animationDelay: '-0.16s' }}></div>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#667eea', animation: 'bounce 1.4s infinite ease-in-out both' }}></div>
              </div>
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Input Area */}
      <div style={{
        padding: '20px 24px',
        background: 'white',
        borderTop: '1px solid #e0e0e0'
      }}>
        <div style={{
          maxWidth: '900px',
          margin: '0 auto',
          display: 'flex',
          gap: '12px',
          alignItems: 'flex-end'
        }}>
          <div style={{
            flex: 1,
            background: '#f7f9fc',
            borderRadius: '24px',
            padding: '12px 20px',
            border: '2px solid #e0e0e0',
            display: 'flex',
            alignItems: 'center',
            gap: '12px'
          }}>
            <textarea
              ref={textareaRef}
              value={step === 'create' ? userRequest : editInstruction}
              onChange={(e) => step === 'create' ? setUserRequest(e.target.value) : setEditInstruction(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={step === 'create' ? "Describe your website..." : "Ask me to edit anything..."}
              style={{
                flex: 1,
                border: 'none',
                outline: 'none',
                background: 'transparent',
                fontSize: '15px',
                fontFamily: 'inherit',
                resize: 'none',
                minHeight: '24px',
                maxHeight: '200px',
                lineHeight: '1.5',
                color: '#000000'
              }}
              rows={1}
            />
            <button
              onClick={step === 'create' ? handleCreateWebsite : handleEditWebsite}
              disabled={isLoading || (step === 'create' ? !userRequest.trim() : !editInstruction.trim())}
              style={{
                width: '40px',
                height: '40px',
                borderRadius: '50%',
                border: 'none',
                background: isLoading ? '#ccc' : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                color: 'white',
                fontSize: '18px',
                cursor: isLoading ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                transition: 'transform 0.2s'
              }}
              onMouseDown={(e) => e.currentTarget.style.transform = 'scale(0.95)'}
              onMouseUp={(e) => e.currentTarget.style.transform = 'scale(1)'}
            >
              {isLoading ? '⏳' : '↑'}
            </button>
          </div>
        </div>
        <p style={{
          maxWidth: '900px',
          margin: '12px auto 0',
          fontSize: '12px',
          color: '#999',
          textAlign: 'center'
        }}>
          Press Enter to send • Shift + Enter for new line
        </p>
      </div>

      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: scale(0); }
          40% { transform: scale(1); }
        }
      `}</style>
    </div>
  );
};

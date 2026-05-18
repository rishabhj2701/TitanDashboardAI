// @ts-nocheck
import React, { useState, useRef, useEffect } from 'react';
import Papa from 'papaparse';
import { chatWebsite, createEditableWebsite, editWebsite } from './api/websiteClient';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

interface SiteState {
  site_id: string;
  name: string;
  description: string;
  version: number;
  header?: any;
  charts?: any[];
  sections?: any[];
  tables?: any[];
}

// Helper function to parse markdown-style formatting
const parseMarkdown = (text: string) => {
  // Replace **text** with bold
  let parsed = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Replace single line breaks with <br/>
  parsed = parsed.replace(/\n/g, '<br/>');
  return parsed;
};

export const UnifiedWebsiteBuilder: React.FC = () => {
  // Step management
  const [step, setStep] = useState<'upload' | 'preview' | 'build' | 'chat'>('upload');

  // Upload step
  const [file, setFile] = useState<File | null>(null);
  const [uploadedData, setUploadedData] = useState<any>(null);
  const [uploading, setUploading] = useState(false);

  // Build step
  const [userRequest, setUserRequest] = useState('');
  const [isBuilding, setIsBuilding] = useState(false);

  // Chat step
  const [siteState, setSiteState] = useState<SiteState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [editInstruction, setEditInstruction] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // Load chat history from localStorage
  useEffect(() => {
    if (siteState?.site_id) {
      const savedMessages = localStorage.getItem(`chat_history_${siteState.site_id}`);
      if (savedMessages) {
        try {
          setMessages(JSON.parse(savedMessages));
        } catch (e) {
          console.error('Failed to load chat history:', e);
        }
      }
    }
  }, [siteState?.site_id]);

  // Save chat history to localStorage
  useEffect(() => {
    if (siteState?.site_id && messages.length > 0) {
      localStorage.setItem(`chat_history_${siteState.site_id}`, JSON.stringify(messages));
    }
  }, [messages, siteState?.site_id]);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [userRequest, editInstruction]);

  // Auto-scroll messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleFileSelect = (selectedFile: File) => {
    setFile(selectedFile);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile && droppedFile.name.endsWith('.csv')) {
      handleFileSelect(droppedFile);
    }
  };

  const calculateStats = (data: any[], columns: string[]) => {
    const statsObj: any = {};
    columns.forEach(col => {
      const values = data.map(row => row[col]);
      const nonNullValues = values.filter(v => v !== null && v !== undefined && v !== '');

      statsObj[col] = {
        count: values.length,
        nulls: values.length - nonNullValues.length,
        unique: new Set(nonNullValues).size
      };
    });
    return statsObj;
  };

  const handleUpload = async () => {
    if (!file) return;

    setUploading(true);

    try {
      Papa.parse(file, {
        header: true,
        dynamicTyping: true,
        skipEmptyLines: true,
        complete: (results) => {
          const data = results.data;
          const columns = results.meta.fields || [];
          const stats = calculateStats(data, columns);

          setUploadedData({
            columns,
            sample_data: data.slice(0, 50),
            stats: stats,
            row_count: data.length
          });
          setStep('preview');
          setUploading(false);
        },
        error: (error) => {
          console.error('Parse failed:', error);
          alert('Failed to parse CSV file');
          setUploading(false);
        }
      });
    } catch (error) {
      console.error('Upload failed:', error);
      alert('Failed to process file');
      setUploading(false);
    }
  };

  const handleConfirmData = () => {
    setStep('build');
  };

  const handleBuildWebsite = async () => {
    if (!userRequest.trim() || !uploadedData) return;

    setIsBuilding(true);
    setMessages([{
      role: 'system',
      content: '🎨 Building your website... This may take a moment.',
      timestamp: Date.now()
    }]);

    try {
      const result = await createEditableWebsite({
        request: userRequest,
        data_columns: uploadedData.columns,
        sample_data: uploadedData.sample_data,
        dataset_stats: uploadedData.stats
      }) as any;
      setSiteState(result.site_state);

      // Save to sessionStorage
      const sitesDataStr = sessionStorage.getItem('generated_sites_data_v2');
      const sitesData = sitesDataStr ? JSON.parse(sitesDataStr) : {};
      sitesData[result.site_state.site_id] = result.site_state;
      sessionStorage.setItem('generated_sites_data_v2', JSON.stringify(sitesData));

      setMessages([{
        role: 'assistant',
        content: `✅ **Website Created!**\n\n**${result.site_state.header?.title || result.site_state.name}**\n${result.site_state.header?.subtitle || result.site_state.description}\n\n📊 **Components:**\n• ${result.site_state.sections?.length || 0} sections\n• ${result.site_state.charts?.length || 0} charts\n• ${result.site_state.tables?.length || 0} tables\n\n🔗 View Website: ${window.location.origin}/sites/${result.site_state.site_id}\n\nI'm ready to help you edit! Try:\n• "Add a dark header"\n• "Change the title"\n• "Add more charts"`,
        timestamp: Date.now()
      }]);

      setStep('chat');
      setUserRequest('');
    } catch (error) {
      console.error('Failed to build website:', error);
      setMessages([{
        role: 'system',
        content: `❌ Failed to build website: ${error instanceof Error ? error.message : 'Unknown error'}`,
        timestamp: Date.now()
      }]);
    } finally {
      setIsBuilding(false);
    }
  };

  const handleEditWebsite = async () => {
    if (!siteState || !editInstruction.trim()) return;

    setIsLoading(true);
    setMessages(prev => [...prev, {
      role: 'user',
      content: editInstruction,
      timestamp: Date.now()
    }]);

    // Check if this is a question/conversation vs an edit command
    const instruction = editInstruction.toLowerCase();
    const isQuestion =
      instruction.includes('what') ||
      instruction.includes('how') ||
      instruction.includes('why') ||
      instruction.includes('where') ||
      instruction.includes('when') ||
      instruction.includes('which') ||
      instruction.includes('who') ||
      instruction.includes('do we have') ||
      instruction.includes('is there') ||
      instruction.includes('are there') ||
      instruction.includes('does') ||
      instruction.includes('can you tell') ||
      instruction.includes('explain') ||
      instruction.includes('show me') ||
      instruction.includes('tell me') ||
      instruction.includes('?');

    const isEditCommand =
      instruction.includes('add a') ||
      instruction.includes('add more') ||
      instruction.includes('remove') ||
      instruction.includes('delete') ||
      instruction.includes('change the') ||
      instruction.includes('update the') ||
      instruction.includes('edit') ||
      instruction.includes('make it') ||
      instruction.includes('make the') ||
      instruction.includes('create a') ||
      instruction.includes('move');

    // If it's clearly a question and not an edit command, respond conversationally
    if (isQuestion && !isEditCommand) {
      try {
        // Get info about the dataset and website with sample data
        const sampleData = uploadedData?.sample_data?.slice(0, 3) || [];
        const dataInfo = `Dataset columns: ${uploadedData?.columns?.join(', ') || 'Unknown columns'}
Row count: ${uploadedData?.row_count || 'Unknown'}

Sample data (first 3 rows):
${JSON.stringify(sampleData, null, 2)}

Current charts: ${siteState.charts?.map((c: any) => `${c.title} (${c.type})`).join(', ') || 'None'}`;

        const result = await chatWebsite({
          message: editInstruction,
          context: dataInfo
        }) as any;
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: result.response,
          timestamp: Date.now()
        }]);
        setEditInstruction('');
        setIsLoading(false);
        return;
      } catch (error) {
        console.log('Chat endpoint not available, falling back to edit');
      }
    }

    // Otherwise, treat as edit command
    try {
      const result = await editWebsite({
        site_id: siteState.site_id,
        instruction: editInstruction
      }) as any;

      // Check if there was an error or no changes
      if (result.error || result.actions.length === 0) {
        // Show error or info message without version/link
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: result.message,
          timestamp: Date.now()
        }]);
        setEditInstruction('');
        setIsLoading(false);
        return;
      }

      // Changes were made - update state
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (step === 'build') {
        handleBuildWebsite();
      } else if (step === 'chat') {
        handleEditWebsite();
      }
    }
  };

  // STEP 1: UPLOAD
  if (step === 'upload') {
    return (
      <div style={{
        position: 'fixed',
        top: '60px',
        left: 0,
        right: 0,
        bottom: 0,
        background: 'linear-gradient(135deg, rgba(10, 14, 39, 0.95), rgba(20, 25, 50, 0.95))',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '40px'
      }}>
        <div style={{
          background: 'rgba(20, 25, 50, 0.95)',
          borderRadius: '24px',
          padding: '60px',
          maxWidth: '600px',
          width: '100%',
          boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
          border: '1px solid rgba(100, 255, 218, 0.3)'
        }}>
          <div style={{ textAlign: 'center', marginBottom: '40px' }}>
            <div style={{ fontSize: '64px', marginBottom: '20px' }}>🎨</div>
            <h1 style={{ fontSize: '32px', fontWeight: 700, color: '#64ffda', marginBottom: '12px' }}>
              AI Website Builder
            </h1>
            <p style={{ fontSize: '16px', color: '#ccd6f6' }}>
              Upload your dataset and let AI build you a beautiful dashboard
            </p>
          </div>

          <div
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: '3px dashed #64ffda',
              borderRadius: '16px',
              padding: '60px 40px',
              textAlign: 'center',
              cursor: 'pointer',
              background: file ? 'rgba(100, 255, 218, 0.1)' : 'rgba(30, 30, 50, 0.3)',
              transition: 'all 0.3s ease'
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              onChange={(e) => e.target.files?.[0] && handleFileSelect(e.target.files[0])}
              style={{ display: 'none' }}
            />
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>
              {file ? '✅' : '📊'}
            </div>
            <p style={{ fontSize: '18px', fontWeight: 600, color: '#64ffda', marginBottom: '8px' }}>
              {file ? file.name : 'Drop your CSV file here'}
            </p>
            <p style={{ fontSize: '14px', color: '#ccd6f6' }}>
              {file ? 'Ready to upload' : 'or click to browse'}
            </p>
          </div>

          {file && (
            <button
              onClick={handleUpload}
              disabled={uploading}
              style={{
                width: '100%',
                marginTop: '24px',
                padding: '16px',
                background: uploading ? 'rgba(100, 100, 100, 0.5)' : 'linear-gradient(135deg, #64ffda 0%, #06b6d4 100%)',
                color: '#0a0e27',
                border: 'none',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: 600,
                cursor: uploading ? 'not-allowed' : 'pointer',
                transition: 'transform 0.2s',
                boxShadow: uploading ? 'none' : '0 4px 20px rgba(100, 255, 218, 0.4)'
              }}
              onMouseDown={(e) => !uploading && (e.currentTarget.style.transform = 'scale(0.98)')}
              onMouseUp={(e) => e.currentTarget.style.transform = 'scale(1)'}
            >
              {uploading ? '⏳ Uploading...' : '📤 Upload Dataset'}
            </button>
          )}
        </div>
      </div>
    );
  }

  // STEP 2: PREVIEW
  if (step === 'preview' && uploadedData) {
    return (
      <div style={{
        position: 'fixed',
        top: '60px',
        left: 0,
        right: 0,
        bottom: 0,
        background: 'linear-gradient(135deg, rgba(10, 14, 39, 0.95), rgba(20, 25, 50, 0.95))',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '40px'
      }}>
        <div style={{
          background: 'rgba(20, 25, 50, 0.95)',
          borderRadius: '24px',
          padding: '60px',
          maxWidth: '800px',
          width: '100%',
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
          border: '1px solid rgba(100, 255, 218, 0.3)'
        }}>
          <div style={{ textAlign: 'center', marginBottom: '40px' }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>✅</div>
            <h2 style={{ fontSize: '28px', fontWeight: 700, color: '#64ffda', marginBottom: '8px' }}>
              Dataset Uploaded!
            </h2>
            <p style={{ fontSize: '16px', color: '#ccd6f6' }}>
              {uploadedData.row_count} rows × {uploadedData.columns.length} columns
            </p>
          </div>

          <div style={{
            background: 'rgba(10, 14, 39, 0.6)',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '24px',
            border: '1px solid rgba(100, 255, 218, 0.2)'
          }}>
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: '#64ffda', marginBottom: '16px' }}>
              📋 Columns ({uploadedData.columns.length})
            </h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              {uploadedData.columns.map((col: string) => (
                <span key={col} style={{
                  background: 'rgba(100, 255, 218, 0.1)',
                  padding: '8px 16px',
                  borderRadius: '8px',
                  fontSize: '14px',
                  color: '#64ffda',
                  fontWeight: 500,
                  border: '1px solid rgba(100, 255, 218, 0.3)'
                }}>
                  {col}
                </span>
              ))}
            </div>
          </div>

          <div style={{
            background: 'rgba(10, 14, 39, 0.6)',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '32px',
            border: '1px solid rgba(100, 255, 218, 0.2)'
          }}>
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: '#64ffda', marginBottom: '16px' }}>
              👀 Sample Data
            </h3>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: '14px', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'rgba(30, 30, 50, 0.5)' }}>
                    {uploadedData.columns.map((col: string) => (
                      <th key={col} style={{
                        padding: '12px',
                        textAlign: 'left',
                        fontWeight: 600,
                        color: '#64ffda',
                        borderBottom: '2px solid rgba(100, 255, 218, 0.3)'
                      }}>
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {uploadedData.sample_data.slice(0, 5).map((row: any, i: number) => (
                    <tr key={i} style={{ background: i % 2 === 0 ? 'rgba(30, 30, 50, 0.4)' : 'rgba(30, 30, 50, 0.2)' }}>
                      {uploadedData.columns.map((col: string) => (
                        <td key={col} style={{
                          padding: '12px',
                          color: '#ccd6f6',
                          borderBottom: '1px solid rgba(100, 255, 218, 0.1)'
                        }}>
                          {String(row[col])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px' }}>
            <button
              onClick={() => setStep('upload')}
              style={{
                flex: 1,
                padding: '16px',
                background: 'rgba(100, 255, 218, 0.1)',
                color: '#64ffda',
                border: '2px solid #64ffda',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
            >
              ← Back
            </button>
            <button
              onClick={handleConfirmData}
              style={{
                flex: 2,
                padding: '16px',
                background: 'linear-gradient(135deg, #64ffda 0%, #06b6d4 100%)',
                color: '#0a0e27',
                border: 'none',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: 600,
                cursor: 'pointer',
                boxShadow: '0 4px 20px rgba(100, 255, 218, 0.4)',
                transition: 'all 0.2s'
              }}
            >
              🚀 Continue to Build
            </button>
          </div>
        </div>
      </div>
    );
  }

  // STEP 3: BUILD
  if (step === 'build') {
    return (
      <div style={{
        position: 'fixed',
        top: '60px',
        left: 0,
        right: 0,
        bottom: 0,
        background: 'linear-gradient(135deg, rgba(10, 14, 39, 0.95), rgba(20, 25, 50, 0.95))',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '40px'
      }}>
        <div style={{
          background: 'rgba(20, 25, 50, 0.95)',
          borderRadius: '28px',
          padding: '60px',
          maxWidth: '800px',
          width: '100%',
          maxHeight: '85vh',
          overflowY: 'auto',
          boxShadow: '0 25px 70px rgba(0,0,0,0.5)',
          border: '1px solid rgba(100, 255, 218, 0.3)'
        }}>
          <div style={{ textAlign: 'center', marginBottom: '40px' }}>
            <div style={{
              fontSize: '72px',
              marginBottom: '20px',
              animation: isBuilding ? 'spin 2s linear infinite' : 'none',
              filter: 'drop-shadow(0 4px 12px rgba(102, 126, 234, 0.3))'
            }}>
              {isBuilding ? '⚙️' : '🎨'}
            </div>
            <h1 style={{
              fontSize: '36px',
              fontWeight: 800,
              background: 'linear-gradient(135deg, #64ffda 0%, #06b6d4 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              marginBottom: '16px',
              letterSpacing: '-0.5px'
            }}>
              {isBuilding ? 'Building Your Website...' : 'Tell Me What Website to Build'}
            </h1>
            <p style={{ fontSize: '17px', color: '#ccd6f6', lineHeight: '1.6' }}>
              {isBuilding ? 'This may take a moment' : 'Describe the website you want, and I\'ll generate it for you'}
            </p>
          </div>

          {!isBuilding && (
            <>
              <textarea
                ref={textareaRef}
                value={userRequest}
                onChange={(e) => setUserRequest(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Build a crash analysis dashboard with severity distribution charts, geographic heatmaps, and temporal trend analysis..."
                style={{
                  width: '100%',
                  minHeight: '140px',
                  padding: '24px',
                  fontSize: '16px',
                  border: '2px solid rgba(100, 255, 218, 0.3)',
                  borderRadius: '16px',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                  marginBottom: '24px',
                  outline: 'none',
                  transition: 'all 0.3s',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                  lineHeight: '1.6',
                  background: 'rgba(10, 14, 39, 0.6)',
                  color: '#ccd6f6'
                }}
                onFocus={(e) => {
                  e.target.style.borderColor = '#64ffda';
                  e.target.style.boxShadow = '0 4px 16px rgba(100, 255, 218, 0.25)';
                }}
                onBlur={(e) => {
                  e.target.style.borderColor = 'rgba(100, 255, 218, 0.3)';
                  e.target.style.boxShadow = '0 2px 8px rgba(0,0,0,0.2)';
                }}
                rows={5}
              />

              <button
                onClick={handleBuildWebsite}
                disabled={!userRequest.trim()}
                style={{
                  width: '100%',
                  padding: '20px',
                  background: userRequest.trim()
                    ? 'linear-gradient(135deg, #64ffda 0%, #06b6d4 100%)'
                    : 'rgba(100, 100, 100, 0.3)',
                  color: userRequest.trim() ? '#0a0e27' : '#999',
                  border: 'none',
                  borderRadius: '14px',
                  fontSize: '18px',
                  fontWeight: 700,
                  cursor: userRequest.trim() ? 'pointer' : 'not-allowed',
                  marginBottom: '32px',
                  transition: 'all 0.3s',
                  boxShadow: userRequest.trim() ? '0 8px 20px rgba(100, 255, 218, 0.4)' : 'none',
                  letterSpacing: '0.5px'
                }}
                onMouseEnter={(e) => {
                  if (userRequest.trim()) {
                    e.currentTarget.style.transform = 'translateY(-2px)';
                    e.currentTarget.style.boxShadow = '0 12px 28px rgba(100, 255, 218, 0.5)';
                  }
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = 'translateY(0)';
                  e.currentTarget.style.boxShadow = userRequest.trim() ? '0 8px 20px rgba(100, 255, 218, 0.4)' : 'none';
                }}
                onMouseDown={(e) => userRequest.trim() && (e.currentTarget.style.transform = 'scale(0.98)')}
                onMouseUp={(e) => e.currentTarget.style.transform = 'scale(1)'}
              >
                🚀 Build Website
              </button>

              <div>
                <p style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: 'rgba(204, 214, 246, 0.6)',
                  marginBottom: '16px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px'
                }}>
                  <span>💡</span>
                  <span>Example Requests</span>
                </p>
                <div style={{
                  maxHeight: '220px',
                  overflowY: 'auto',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '12px',
                  paddingRight: '8px'
                }}>
                  {[
                    '🚧 Workzone Analysis Website - Interactive maps and safety metrics',
                    '🚨 Hard Braking Dashboard - Real-time event tracking and heatmaps',
                    '💥 Crash Hotspot Website - Geographic clustering and risk analysis',
                    '📊 Traffic Flow Analytics - Volume trends and congestion patterns',
                    '⚠️ Safety Incident Tracker - Comprehensive incident analysis dashboard'
                  ].map((example, i) => (
                    <button
                      key={i}
                      onClick={() => setUserRequest(example.split(' - ')[0].substring(3))}
                      style={{
                        padding: '14px 18px',
                        background: 'rgba(30, 30, 50, 0.4)',
                        border: '2px solid rgba(100, 255, 218, 0.3)',
                        borderRadius: '12px',
                        fontSize: '14px',
                        color: '#ccd6f6',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'all 0.2s',
                        boxShadow: '0 2px 6px rgba(0,0,0,0.04)',
                        whiteSpace: 'normal',
                        lineHeight: '1.5'
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'rgba(100, 255, 218, 0.15)';
                        e.currentTarget.style.borderColor = '#64ffda';
                        e.currentTarget.style.boxShadow = '0 4px 12px rgba(100, 255, 218, 0.25)';
                        e.currentTarget.style.transform = 'translateX(4px)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'rgba(30, 30, 50, 0.4)';
                        e.currentTarget.style.borderColor = 'rgba(100, 255, 218, 0.3)';
                        e.currentTarget.style.boxShadow = '0 2px 6px rgba(0,0,0,0.04)';
                        e.currentTarget.style.transform = 'translateX(0)';
                      }}
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {isBuilding && (
            <div style={{
              background: 'rgba(10, 14, 39, 0.6)',
              borderRadius: '12px',
              padding: '24px',
              textAlign: 'center',
              border: '1px solid rgba(100, 255, 218, 0.2)'
            }}>
              <div style={{ fontSize: '14px', color: '#ccd6f6', lineHeight: '1.8' }}>
                🔍 Analyzing your dataset...<br />
                🎨 Generating visualizations...<br />
                ✨ Building your website...
              </div>
            </div>
          )}
        </div>

        <style>{`
          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}</style>
      </div>
    );
  }

  // STEP 4: CHAT
  if (step === 'chat' && siteState) {
    return (
      <div style={{
        position: 'fixed',
        top: '60px',
        left: 0,
        right: 0,
        bottom: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'linear-gradient(135deg, #f5f7fa 0%, #e9ecef 100%)'
      }}>
        {/* Header */}
        <div style={{
          background: 'linear-gradient(135deg, rgba(10, 14, 39, 0.95), rgba(20, 25, 50, 0.95))',
          borderBottom: '1px solid rgba(100, 255, 218, 0.2)',
          padding: '24px 40px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 4px 20px rgba(100, 255, 218, 0.15)'
        }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ fontSize: '20px', fontWeight: 700, margin: 0, color: '#64ffda' }}>
              {siteState.header?.title || siteState.name}
            </h2>
            <p style={{ fontSize: '13px', color: '#ccd6f6', margin: '6px 0 0 0', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <span style={{ background: 'rgba(100, 255, 218, 0.2)', padding: '3px 10px', borderRadius: '12px', color: '#64ffda' }}>v{siteState.version}</span>
              <span>📊 {siteState.charts?.length || 0} charts</span>
              <span>📄 {siteState.sections?.length || 0} sections</span>
            </p>
          </div>
          <a
            href={`/sites/${siteState.site_id}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              padding: '12px 24px',
              background: 'linear-gradient(135deg, #64ffda 0%, #06b6d4 100%)',
              color: '#0a0e27',
              borderRadius: '12px',
              textDecoration: 'none',
              fontSize: '14px',
              fontWeight: 700,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              boxShadow: '0 4px 12px rgba(100, 255, 218, 0.3)',
              transition: 'transform 0.2s, box-shadow 0.2s'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.boxShadow = '0 6px 20px rgba(100, 255, 218, 0.5)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(100, 255, 218, 0.3)';
            }}
          >
            🚀 View Website
          </a>
        </div>

        {/* Chat Messages */}
        <div style={{
          flex: 1,
          overflowY: 'auto',
          padding: '40px',
          display: 'flex',
          flexDirection: 'column',
          background: 'rgba(10, 14, 39, 0.85)'
        }}>
          <div style={{ maxWidth: '900px', margin: '0 auto', width: '100%' }}>
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
                  <div
                    style={{
                      background: msg.role === 'user'
                        ? 'rgba(100, 255, 218, 0.12)'
                        : msg.role === 'assistant'
                        ? 'rgba(30, 30, 50, 0.8)'
                        : 'rgba(100, 149, 237, 0.1)',
                      color: msg.role === 'user' ? '#e0fff6' : '#ccd6f6',
                      padding: '14px 18px',
                      borderRadius: '12px',
                      fontSize: '14px',
                      lineHeight: '1.6',
                      border: msg.role === 'user' ? '1px solid rgba(100, 255, 218, 0.25)' : msg.role === 'system' ? '1px solid rgba(100, 149, 237, 0.3)' : '1px solid rgba(100, 255, 218, 0.1)',
                      backdropFilter: 'blur(10px)',
                      transition: 'all 0.2s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = msg.role === 'user' ? 'rgba(100, 255, 218, 0.4)' : 'rgba(100, 255, 218, 0.2)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = msg.role === 'user' ? 'rgba(100, 255, 218, 0.25)' : msg.role === 'system' ? 'rgba(100, 149, 237, 0.3)' : 'rgba(100, 255, 218, 0.1)';
                    }}
                    dangerouslySetInnerHTML={{ __html: parseMarkdown(msg.content) }}
                  />
                </div>
              </div>
            ))}
            {isLoading && (
              <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: '24px' }}>
                <div style={{
                  background: 'rgba(30, 30, 50, 0.6)',
                  padding: '14px 18px',
                  borderRadius: '18px 18px 18px 4px',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
                  border: '1px solid rgba(100, 255, 218, 0.15)'
                }}>
                  <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: '#64ffda',
                      animation: 'bounce 1.4s infinite ease-in-out both',
                      animationDelay: '-0.32s'
                    }}></div>
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: '#64ffda',
                      animation: 'bounce 1.4s infinite ease-in-out both',
                      animationDelay: '-0.16s'
                    }}></div>
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: '#64ffda',
                      animation: 'bounce 1.4s infinite ease-in-out both'
                    }}></div>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input Area */}
        <div style={{
          background: 'rgba(10, 14, 39, 0.95)',
          borderTop: '1px solid rgba(100, 255, 218, 0.2)',
          padding: '20px 40px 24px',
          backdropFilter: 'blur(10px)'
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
              background: 'rgba(30, 30, 50, 0.7)',
              borderRadius: '16px',
              padding: '12px 20px',
              border: '1px solid rgba(100, 255, 218, 0.25)',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              transition: 'all 0.3s'
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = 'rgba(100, 255, 218, 0.5)';
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = 'rgba(100, 255, 218, 0.25)';
            }}
            >
              <textarea
                ref={textareaRef}
                value={editInstruction}
                onChange={(e) => setEditInstruction(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask me anything or tell me to edit..."
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
                  lineHeight: '1.6',
                  color: '#ccd6f6'
                }}
                rows={1}
              />
              <button
                onClick={handleEditWebsite}
                disabled={isLoading || !editInstruction.trim()}
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '8px',
                  border: 'none',
                  background: isLoading || !editInstruction.trim()
                    ? 'rgba(100, 100, 100, 0.3)'
                    : '#64ffda',
                  color: '#0a0e27',
                  fontSize: '18px',
                  cursor: isLoading || !editInstruction.trim() ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  transition: 'all 0.2s',
                  fontWeight: 'bold'
                }}
                onMouseEnter={(e) => {
                  if (!isLoading && editInstruction.trim()) {
                    e.currentTarget.style.background = '#7ffff0';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isLoading && editInstruction.trim()) {
                    e.currentTarget.style.background = '#64ffda';
                  }
                }}
              >
                {isLoading ? '⏳' : '↑'}
              </button>
            </div>
          </div>
          <p style={{
            maxWidth: '900px',
            margin: '14px auto 0',
            fontSize: '12px',
            color: 'rgba(204, 214, 246, 0.5)',
            textAlign: 'center',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '16px'
          }}>
            <span>⌨️ Press Enter to send</span>
            <span>•</span>
            <span>⇧ Shift + Enter for new line</span>
            <span>•</span>
            <span>💾 Chat history saved</span>
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
  }

  return null;
};

// @ts-nocheck
import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { FileUpload } from '../../components/FileUpload';
import { generateWebsite } from './api/websiteClient';

interface GeneratedWebsite {
  site_id: string;
  name: string;
  description: string;
  url: string;
  created_at: Date;
  data: any[];
  components: any;
}

const WB_STATE_KEY = 'website_builder_state_v2'; // Different key to avoid conflicts

export const WebsiteBuilder: React.FC = () => {
  const [step, setStep] = useState<'upload' | 'request' | 'preview'>('upload');
  const [uploadedData, setUploadedData] = useState<any>(null);
  const [userRequest, setUserRequest] = useState('');
  const [generatedSite, setGeneratedSite] = useState<GeneratedWebsite | null>(null);
  const [loading, setLoading] = useState(false);
  const [allSites, setAllSites] = useState<GeneratedWebsite[]>([]);

  // Load saved state on mount (from sessionStorage - clears on refresh)
  useEffect(() => {
    // Clean up old storage keys
    sessionStorage.removeItem('website_builder_state');
    localStorage.removeItem('website_builder_state');

    try {
      const saved = sessionStorage.getItem(WB_STATE_KEY);
      if (saved) {
        const state = JSON.parse(saved);
        if (state.uploadedData) setUploadedData(state.uploadedData);
        if (state.userRequest) setUserRequest(state.userRequest);
        if (state.step) setStep(state.step);
        if (state.generatedSite) setGeneratedSite(state.generatedSite);
        if (state.allSites) setAllSites(state.allSites);
        console.log('🌐 Restored website builder state');
      }
    } catch (error) {
      console.error('Failed to load website builder state:', error);
    }
  }, []);

  // Save state whenever it changes (to sessionStorage - clears on refresh)
  useEffect(() => {
    try {
      sessionStorage.setItem(WB_STATE_KEY, JSON.stringify({
        step,
        uploadedData,
        userRequest,
        generatedSite,
        allSites,
      }));
    } catch (error) {
      console.error('Failed to save website builder state:', error);
    }
  }, [step, uploadedData, userRequest, generatedSite, allSites]);

  const handleFileUpload = (file: any) => {
    setUploadedData(file);
    setStep('request');
  };

  const handleBuildWebsite = async () => {
    if (!userRequest.trim()) {
      alert('Please describe the website you want to build');
      return;
    }

    setLoading(true);

    try {
      // Transform stats array to dict format expected by backend
      const datasetStats = uploadedData?.stats
        ? {
            row_count: uploadedData.data?.length || 0,
            column_count: uploadedData.columns?.length || 0,
            columns: uploadedData.stats.reduce((acc: any, stat: any) => {
              acc[stat.name] = {
                type: stat.type,
                count: stat.count,
                nulls: stat.nulls,
                unique: stat.unique
              };
              return acc;
            }, {})
          }
        : { row_count: 0, column_count: 0, columns: {} };

      // Call backend to generate website
      const result = await generateWebsite({
        request: userRequest,
        data_columns: uploadedData?.columns || [],
        sample_data: uploadedData?.data || [],
        dataset_stats: datasetStats
      }) as any;
      console.log('Result:', result);

      if (result.status === 'success') {
        console.log('✅ Website generated successfully!');
        console.log('Website name:', result.website.name);
        console.log('Website URL:', result.website.url);

        const siteId = result.website.site_id || `site-${Date.now()}`;
        const newSite: GeneratedWebsite = {
          site_id: siteId,
          name: result.website.name || 'Generated Website',
          description: result.website.description || 'AI Generated Website',
          url: result.website.url || `/sites/${siteId}`,
          created_at: new Date(),
          data: uploadedData?.data || [],
          components: result.website.components || {}
        };

        // Also save in a format compatible with GeneratedSite component
        const compatibleSiteData = {
          ...newSite,
          plan: result.website.plan || {},
          ui_design: result.website.ui_design || {},
          data_insights: result.website.data_insights || {},
          charts: result.website.charts || [],
          code: result.website.code || {},
          data_columns: uploadedData?.columns || [],
          sample_data: uploadedData?.data || [],
          dataset_stats: datasetStats,
        };

        // Store in sessionStorage for GeneratedSite to access
        const existingSites = sessionStorage.getItem('generated_sites_data_v2');
        const sitesData = existingSites ? JSON.parse(existingSites) : {};
        sitesData[siteId] = compatibleSiteData;
        sessionStorage.setItem('generated_sites_data_v2', JSON.stringify(sitesData));

        console.log('✅ Saved site data to sessionStorage:');
        console.log('  Site ID:', siteId);
        console.log('  Site URL:', compatibleSiteData.url);
        console.log('  All stored sites:', Object.keys(sitesData));
        console.log('  Full data:', compatibleSiteData);

        // Verify it was saved
        const verification = sessionStorage.getItem('generated_sites_data_v2');
        console.log('🔍 Verification - data still in storage:', !!verification);
        if (verification) {
          const parsed = JSON.parse(verification);
          console.log('  Verified site IDs:', Object.keys(parsed));
        }
        console.log('Setting generated site:', newSite);
        setGeneratedSite(newSite);
        setAllSites(prev => [...prev, newSite]);
        console.log('Switching to preview step...');
        setStep('preview');
      } else {
        throw new Error(result.message || 'Website generation failed');
      }
    } catch (error) {
      console.error('Failed to generate website:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      alert(`Failed to generate website: ${errorMessage}\n\nPlease check the browser console (F12) for details.`);
    } finally {
      setLoading(false);
    }
  };

  const renderMetrics = (data: any[]) => {
    if (!data || data.length === 0) return null;

    const columns = Object.keys(data[0] || {});
    const totalRecords = data.length;
    const uniqueValues = columns.length > 0
      ? new Set(data.map(d => d[columns[0]])).size
      : 0;

    return (
      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-value">{totalRecords}</div>
          <div className="metric-label">Total Records</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{columns.length}</div>
          <div className="metric-label">Columns</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{uniqueValues}</div>
          <div className="metric-label">Unique Values</div>
        </div>
      </div>
    );
  };

  const renderDataTable = (data: any[]) => {
    if (!data || data.length === 0) return null;

    const columns = Object.keys(data[0]);

    return (
      <div className="data-table-section">
        <h3>Data Preview</h3>
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                {columns.slice(0, 6).map(col => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.slice(0, 10).map((row, i) => (
                <tr key={i}>
                  {columns.slice(0, 6).map(col => (
                    <td key={col}>{String(row[col] || '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="website-builder">
      {/* Navigation */}
      <div className="builder-nav">
        <div className="builder-logo">
          <h2>🚀 AI Website Builder</h2>
          <p>Build custom traffic analysis websites instantly</p>
        </div>

        {allSites.length > 0 && (
          <div className="my-sites-dropdown">
            <button className="dropdown-toggle">
              My Websites ({allSites.length})
            </button>
            <div className="dropdown-menu">
              {allSites.map(site => (
                <div
                  key={site.site_id}
                  className="dropdown-item"
                  onClick={() => setGeneratedSite(site)}
                >
                  <strong>{site.name}</strong>
                  <span>{site.url}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Main Content */}
      {step === 'upload' && (
        <div className="step-container">
          <div className="step-header">
            <h1>Step 1: Upload Your Data</h1>
            <p>Upload traffic data (CSV, Excel, GeoJSON) to build your website</p>
          </div>
          <FileUpload onFileProcessed={handleFileUpload} />
          {uploadedData && (
            <button className="btn-next" onClick={() => setStep('request')}>
              Next: Describe Your Website →
            </button>
          )}
        </div>
      )}

      {step === 'request' && (
        <div className="step-container">
          <div className="step-header">
            <h1>Tell Me What Website to Build</h1>
            <p>Describe the website you want, and I'll generate it for you</p>
          </div>

          <div className="request-box">
            <textarea
              placeholder="Example: Build me a workzone analysis website with speed drop charts, queue detection, and interactive maps showing all work zones in the St. Louis area"
              value={userRequest}
              onChange={(e) => setUserRequest(e.target.value)}
              rows={6}
              className="request-input"
            />

            <div className="request-actions">
              <button onClick={() => setStep('upload')} className="btn-back">
                ← Back
              </button>
              <button
                onClick={handleBuildWebsite}
                disabled={loading || !userRequest.trim()}
                className="btn-build"
              >
                {loading ? '🔨 Building Website...' : '🚀 Build Website'}
              </button>
            </div>
          </div>

          <div className="examples-section">
            <h3>💡 Example Requests:</h3>
            <div className="example-pills">
              <div
                className="example-pill"
                onClick={() => setUserRequest('Build me a workzone analysis website with speed drop charts and queue detection')}
              >
                🚧 Workzone Analysis Website
              </div>
              <div
                className="example-pill"
                onClick={() => setUserRequest('Create a hard braking analyzer with hourly patterns and severity breakdown')}
              >
                🚨 Hard Braking Dashboard
              </div>
              <div
                className="example-pill"
                onClick={() => setUserRequest('Build a crash hotspot website with heatmaps and temporal analysis')}
              >
                💥 Crash Hotspot Website
              </div>
              <div
                className="example-pill"
                onClick={() => setUserRequest('Make a traffic signal performance website with queue analysis')}
              >
                🚦 Signal Performance Site
              </div>
            </div>
          </div>
        </div>
      )}

      {step === 'preview' && generatedSite && (
        <div className="preview-container">
          <div className="preview-header">
            <div className="site-info">
              <h1>{generatedSite.name}</h1>
              <p>{generatedSite.description}</p>
              <div className="site-meta">
                <span className="site-url">🔗 {generatedSite.url}</span>
                <span className="site-date">Created: {generatedSite.created_at.toLocaleString()}</span>
              </div>
            </div>
            <div className="preview-actions">
              <button onClick={() => setStep('request')} className="btn-new">
                + New Website
              </button>
              <button className="btn-export">💾 Export</button>
              <button className="btn-deploy">🌐 Deploy</button>
            </div>
          </div>

          <div className="preview-content">
            {/* Metrics */}
            {renderMetrics(generatedSite.data)}

            {/* Data Table */}
            {renderDataTable(generatedSite.data)}

            {/* Website URL */}
            <div className="website-url-section">
              <h3>Your Website is Ready!</h3>
              <div className="url-box">
                <input
                  type="text"
                  value={generatedSite.url ? `${window.location.origin}${generatedSite.url}` : 'Generating URL...'}
                  readOnly
                />
                <button onClick={() => {
                  if (generatedSite.url) {
                    navigator.clipboard.writeText(`${window.location.origin}${generatedSite.url}`);
                    alert('URL copied!');
                  }
                }}>
                  📋 Copy
                </button>
              </div>
              {generatedSite.url && (
                <Link
                  to={generatedSite.url}
                  style={{
                    display: 'inline-block',
                    marginTop: '16px',
                    padding: '12px 24px',
                    background: 'linear-gradient(135deg, #64ffda, #06b6d4)',
                    color: '#0a0e27',
                    textDecoration: 'none',
                    borderRadius: '8px',
                    fontWeight: 600,
                    transition: 'transform 0.2s',
                  }}
                  onMouseOver={(e) => e.currentTarget.style.transform = 'scale(1.05)'}
                  onMouseOut={(e) => e.currentTarget.style.transform = 'scale(1)'}
                >
                  🚀 View Website
                </Link>
              )}
              <p className="url-help">Click the button above to view your generated website</p>
            </div>
          </div>
        </div>
      )}

      <style>{`
        .website-builder {
          min-height: calc(100vh - 60px);
          height: calc(100vh - 60px);
          overflow-y: auto;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }

        .builder-nav {
          background: rgba(255,255,255,0.1);
          backdrop-filter: blur(10px);
          padding: 20px 40px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          border-bottom: 1px solid rgba(255,255,255,0.2);
        }

        .builder-logo h2 {
          margin: 0;
          color: white;
          font-size: 28px;
        }

        .builder-logo p {
          margin: 4px 0 0 0;
          color: rgba(255,255,255,0.9);
          font-size: 14px;
        }

        .my-sites-dropdown {
          position: relative;
        }

        .dropdown-toggle {
          padding: 12px 24px;
          background: rgba(255,255,255,0.2);
          border: 2px solid rgba(255,255,255,0.3);
          color: white;
          border-radius: 8px;
          cursor: pointer;
          font-weight: 600;
          transition: all 0.3s ease;
        }

        .dropdown-toggle:hover {
          background: rgba(255,255,255,0.3);
        }

        .step-container {
          max-width: 1000px;
          margin: 0 auto;
          padding: 60px 20px;
        }

        .step-header {
          text-align: center;
          color: white;
          margin-bottom: 48px;
        }

        .step-header h1 {
          font-size: 48px;
          margin: 0 0 16px 0;
        }

        .step-header p {
          font-size: 20px;
          opacity: 0.95;
        }

        .request-box {
          background: white;
          border-radius: 16px;
          padding: 40px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.2);
          margin-bottom: 40px;
        }

        .request-input {
          width: 100%;
          padding: 20px;
          border: 2px solid #e0e0e0;
          border-radius: 12px;
          font-size: 16px;
          font-family: inherit;
          resize: vertical;
          margin-bottom: 24px;
          line-height: 1.6;
        }

        .request-input:focus {
          outline: none;
          border-color: #667eea;
        }

        .request-actions {
          display: flex;
          gap: 16px;
          justify-content: flex-end;
        }

        .btn-back,
        .btn-build,
        .btn-next,
        .btn-new,
        .btn-export,
        .btn-deploy {
          padding: 16px 32px;
          border: none;
          border-radius: 8px;
          font-size: 16px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .btn-back {
          background: #e0e0e0;
          color: #333;
        }

        .btn-build {
          background: linear-gradient(90deg, #667eea, #764ba2);
          color: white;
          flex: 1;
        }

        .btn-build:hover:not(:disabled) {
          transform: translateY(-2px);
          box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
        }

        .btn-build:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .btn-next {
          background: linear-gradient(90deg, #667eea, #764ba2);
          color: white;
          margin-top: 24px;
          width: 100%;
        }

        .examples-section {
          color: white;
        }

        .examples-section h3 {
          text-align: center;
          margin-bottom: 24px;
          font-size: 24px;
        }

        .example-pills {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 16px;
        }

        .example-pill {
          background: rgba(255,255,255,0.1);
          backdrop-filter: blur(10px);
          border: 2px solid rgba(255,255,255,0.2);
          padding: 20px;
          border-radius: 12px;
          cursor: pointer;
          transition: all 0.3s ease;
          text-align: center;
          font-size: 16px;
        }

        .example-pill:hover {
          background: rgba(255,255,255,0.2);
          transform: translateY(-4px);
        }

        .preview-container {
          max-width: 1400px;
          margin: 0 auto;
          padding: 40px 20px;
        }

        .preview-header {
          background: white;
          border-radius: 16px;
          padding: 32px;
          margin-bottom: 24px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.2);
        }

        .site-info h1 {
          margin: 0 0 8px 0;
          color: #333;
        }

        .site-info p {
          margin: 0 0 16px 0;
          color: #666;
          font-size: 16px;
        }

        .site-meta {
          display: flex;
          gap: 24px;
          flex-wrap: wrap;
        }

        .site-url,
        .site-date {
          padding: 8px 16px;
          background: #f0f0f0;
          border-radius: 6px;
          font-size: 14px;
        }

        .preview-actions {
          display: flex;
          gap: 12px;
          margin-top: 24px;
        }

        .btn-new {
          background: linear-gradient(90deg, #667eea, #764ba2);
          color: white;
        }

        .btn-export,
        .btn-deploy {
          background: white;
          border: 2px solid #667eea;
          color: #667eea;
        }

        .preview-content {
          background: white;
          border-radius: 16px;
          padding: 32px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.2);
        }

        .metrics-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 24px;
          margin-bottom: 32px;
        }

        .metric-card {
          background: linear-gradient(135deg, #667eea, #764ba2);
          color: white;
          padding: 32px;
          border-radius: 12px;
          text-align: center;
        }

        .metric-value {
          font-size: 48px;
          font-weight: 700;
          margin-bottom: 8px;
        }

        .metric-label {
          font-size: 14px;
          opacity: 0.9;
          text-transform: uppercase;
          letter-spacing: 1px;
        }

        .data-table-section {
          margin-bottom: 32px;
        }

        .data-table-section h3 {
          margin-top: 0;
          margin-bottom: 16px;
          color: #333;
        }

        .table-wrapper {
          overflow-x: auto;
        }

        table {
          width: 100%;
          border-collapse: collapse;
        }

        th, td {
          padding: 12px;
          text-align: left;
          border-bottom: 1px solid #e0e0e0;
        }

        th {
          background: #f9f9f9;
          font-weight: 600;
          color: #333;
        }

        .website-url-section {
          background: #f9f9f9;
          padding: 24px;
          border-radius: 12px;
        }

        .website-url-section h3 {
          margin-top: 0;
          color: #333;
        }

        .url-box {
          display: flex;
          gap: 12px;
          margin-bottom: 12px;
        }

        .url-box input {
          flex: 1;
          padding: 12px;
          border: 2px solid #e0e0e0;
          border-radius: 6px;
          font-family: monospace;
          font-size: 14px;
        }

        .url-box button {
          padding: 12px 24px;
          background: #667eea;
          color: white;
          border: none;
          border-radius: 6px;
          cursor: pointer;
          font-weight: 600;
        }

        .url-help {
          margin: 0;
          color: #666;
          font-size: 14px;
        }
      `}</style>
    </div>
  );
};

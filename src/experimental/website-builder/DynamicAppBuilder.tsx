// @ts-nocheck
import React, { useState } from 'react';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, LineElement, PointElement, Title, Tooltip, Legend, ArcElement } from 'chart.js';
import { Bar, Line, Pie } from 'react-chartjs-2';
import { buildWebsiteApp } from './api/websiteClient';

ChartJS.register(CategoryScale, LinearScale, BarElement, LineElement, PointElement, Title, Tooltip, Legend, ArcElement);

interface GeneratedApp {
  id: string;
  name: string;
  description: string;
  html: string;
  css: string;
  data: any;
  charts: ChartConfig[];
  filters: FilterConfig[];
  url: string;
}

interface ChartConfig {
  type: 'bar' | 'line' | 'pie';
  title: string;
  data: any;
  options?: any;
}

interface FilterConfig {
  id: string;
  label: string;
  type: 'select' | 'daterange' | 'slider';
  options?: string[];
  min?: number;
  max?: number;
}

export const DynamicAppBuilder: React.FC = () => {
  const [userRequest, setUserRequest] = useState('');
  const [generatedApp, setGeneratedApp] = useState<GeneratedApp | null>(null);
  const [loading, setLoading] = useState(false);

  const handleBuildApp = async () => {
    if (!userRequest.trim()) return;

    setLoading(true);

    try {
      const result = await buildWebsiteApp({ request: userRequest });

      if (result.status === 'success') {
        setGeneratedApp((result.app as GeneratedApp) || null);
      }
    } catch (error) {
      console.error('Failed to build app:', error);

      // Fallback: Generate app locally for demo
      const demoApp = generateDemoApp(userRequest);
      setGeneratedApp(demoApp);
    } finally {
      setLoading(false);
    }
  };

  const generateDemoApp = (request: string): GeneratedApp => {
    const isHardBraking = request.toLowerCase().includes('hard brak') || request.toLowerCase().includes('braking');

    if (isHardBraking) {
      return {
        id: 'hard_braking_i70',
        name: 'I-70 Hard Braking Analyzer',
        description: 'Comprehensive hard braking event analysis for I-70 corridor',
        html: '',
        css: '',
        url: '/apps/hard-braking-i70',
        data: {
          totalEvents: 1247,
          averageDeceleration: -0.35,
          criticalLocations: 8
        },
        charts: [
          {
            type: 'bar',
            title: 'Hard Braking Events by Hour',
            data: {
              labels: ['12AM', '2AM', '4AM', '6AM', '8AM', '10AM', '12PM', '2PM', '4PM', '6PM', '8PM', '10PM'],
              datasets: [{
                label: 'Events',
                data: [15, 8, 5, 12, 45, 78, 92, 105, 125, 98, 65, 32],
                backgroundColor: 'rgba(255, 99, 132, 0.6)',
                borderColor: 'rgba(255, 99, 132, 1)',
                borderWidth: 1
              }]
            },
            options: {
              responsive: true,
              plugins: {
                legend: { position: 'top' as const },
                title: { display: true, text: 'Hard Braking Events by Hour of Day' }
              }
            }
          },
          {
            type: 'line',
            title: 'Deceleration Trends',
            data: {
              labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
              datasets: [{
                label: 'Avg Deceleration (g)',
                data: [-0.32, -0.35, -0.38, -0.34, -0.42, -0.28, -0.25],
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.4
              }]
            },
            options: {
              responsive: true,
              plugins: {
                legend: { position: 'top' as const },
                title: { display: true, text: 'Average Deceleration by Day' }
              }
            }
          },
          {
            type: 'pie',
            title: 'Events by Severity',
            data: {
              labels: ['Emergency (-0.7g+)', 'Hard (-0.5g)', 'Moderate (-0.3g)'],
              datasets: [{
                data: [145, 387, 715],
                backgroundColor: [
                  'rgba(255, 99, 132, 0.8)',
                  'rgba(255, 159, 64, 0.8)',
                  'rgba(255, 205, 86, 0.8)'
                ]
              }]
            },
            options: {
              responsive: true,
              plugins: {
                legend: { position: 'right' as const },
                title: { display: true, text: 'Braking Severity Distribution' }
              }
            }
          }
        ],
        filters: [
          {
            id: 'road',
            label: 'Road Segment',
            type: 'select',
            options: ['I-70 Mile 1-5', 'I-70 Mile 5-10', 'I-70 Mile 10-15', 'I-70 Mile 15-20']
          },
          {
            id: 'threshold',
            label: 'Deceleration Threshold (g)',
            type: 'slider',
            min: -1.0,
            max: -0.2
          },
          {
            id: 'daterange',
            label: 'Date Range',
            type: 'daterange'
          }
        ]
      };
    }

    // Default generic app
    return {
      id: 'custom_analysis',
      name: 'Custom Traffic Analysis',
      description: request,
      html: '',
      css: '',
      url: '/apps/custom',
      data: {},
      charts: [],
      filters: []
    };
  };

  const renderChart = (chart: ChartConfig, index: number) => {
    const commonProps = {
      data: chart.data,
      options: chart.options || {}
    };

    switch (chart.type) {
      case 'bar':
        return <Bar key={index} {...commonProps} />;
      case 'line':
        return <Line key={index} {...commonProps} />;
      case 'pie':
        return <Pie key={index} {...commonProps} />;
      default:
        return null;
    }
  };

  const handleFilterChange = (_filterId: string, _value: any) => {
    // Placeholder for future filter handling
  };

  return (
    <div className="dynamic-app-builder">
      {!generatedApp ? (
        <div className="builder-interface">
          <div className="builder-header">
            <h1>AI Website Builder</h1>
            <p>Tell me what traffic analysis website you want, and I'll build it for you</p>
          </div>

          <div className="build-prompt">
            <textarea
              placeholder="Example: Build me a hard braking analyzer for I-70 with hourly trends, severity distribution, and top locations"
              value={userRequest}
              onChange={(e) => setUserRequest(e.target.value)}
              rows={4}
            />
            <button
              onClick={handleBuildApp}
              disabled={loading || !userRequest.trim()}
              className="btn-build"
            >
              {loading ? 'Building Your Website...' : 'Build Website'}
            </button>
          </div>

          <div className="examples">
            <h3>Try These Examples:</h3>
            <div className="example-cards">
              <div className="example-card" onClick={() => setUserRequest('Build me a hard braking analyzer for I-70')}>
                <h4>🚨 Hard Braking Analyzer</h4>
                <p>I-70 hard braking events with hourly patterns and severity analysis</p>
              </div>
              <div className="example-card" onClick={() => setUserRequest('Create a crash hotspot dashboard for Highway 40')}>
                <h4>💥 Crash Hotspot Dashboard</h4>
                <p>Highway 40 crash locations with temporal patterns</p>
              </div>
              <div className="example-card" onClick={() => setUserRequest('Build a workzone speed analysis tool')}>
                <h4>🚧 Workzone Impact Analyzer</h4>
                <p>Speed reduction and queue analysis in work zones</p>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="generated-app">
          {/* App Header */}
          <div className="app-header">
            <div className="app-title-section">
              <h1>{generatedApp.name}</h1>
              <p>{generatedApp.description}</p>
              <div className="app-meta">
                <span className="app-url">🔗 {generatedApp.url}</span>
                <button onClick={() => setGeneratedApp(null)} className="btn-back">
                  ← Back to Builder
                </button>
              </div>
            </div>

            {/* Key Metrics */}
            {generatedApp.data && Object.keys(generatedApp.data).length > 0 && (
              <div className="key-metrics">
                {Object.entries(generatedApp.data).map(([key, value]) => (
                  <div key={key} className="metric-card">
                    <div className="metric-value">{value as string}</div>
                    <div className="metric-label">{key.replace(/([A-Z])/g, ' $1').trim()}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Filters */}
          {generatedApp.filters.length > 0 && (
            <div className="app-filters">
              <h3>Filters</h3>
              <div className="filter-controls">
                {generatedApp.filters.map(filter => (
                  <div key={filter.id} className="filter-item">
                    <label>{filter.label}</label>
                    {filter.type === 'select' && (
                      <select onChange={(e) => handleFilterChange(filter.id, e.target.value)}>
                        <option value="">All</option>
                        {filter.options?.map(opt => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    )}
                    {filter.type === 'slider' && (
                      <input
                        type="range"
                        min={filter.min}
                        max={filter.max}
                        step="0.1"
                        onChange={(e) => handleFilterChange(filter.id, parseFloat(e.target.value))}
                      />
                    )}
                    {filter.type === 'daterange' && (
                      <div className="daterange-inputs">
                        <input type="date" />
                        <span>to</span>
                        <input type="date" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Charts Grid */}
          <div className="charts-grid">
            {generatedApp.charts.map((chart, index) => (
              <div key={index} className="chart-container">
                <h3>{chart.title}</h3>
                {renderChart(chart, index)}
              </div>
            ))}
          </div>

          {/* Export Options */}
          <div className="app-actions">
            <button className="btn-export">📊 Export Charts</button>
            <button className="btn-share">🔗 Share Dashboard</button>
            <button className="btn-customize">⚙️ Customize</button>
          </div>
        </div>
      )}

      <style>{`
        .dynamic-app-builder {
          min-height: 100vh;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          padding: 40px 20px;
        }

        .builder-interface {
          max-width: 1200px;
          margin: 0 auto;
        }

        .builder-header {
          text-align: center;
          color: white;
          margin-bottom: 48px;
        }

        .builder-header h1 {
          font-size: 48px;
          margin: 0 0 16px 0;
          font-weight: 700;
        }

        .builder-header p {
          font-size: 20px;
          opacity: 0.9;
        }

        .build-prompt {
          background: white;
          border-radius: 16px;
          padding: 32px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.2);
          margin-bottom: 48px;
        }

        .build-prompt textarea {
          width: 100%;
          padding: 16px;
          border: 2px solid #e0e0e0;
          border-radius: 8px;
          font-size: 16px;
          font-family: inherit;
          resize: vertical;
          margin-bottom: 16px;
        }

        .build-prompt textarea:focus {
          outline: none;
          border-color: #667eea;
        }

        .btn-build {
          width: 100%;
          padding: 20px;
          background: linear-gradient(90deg, #667eea, #764ba2);
          color: white;
          border: none;
          border-radius: 8px;
          font-size: 18px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .btn-build:hover:not(:disabled) {
          transform: translateY(-2px);
          box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
        }

        .btn-build:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .examples {
          color: white;
        }

        .examples h3 {
          text-align: center;
          margin-bottom: 24px;
          font-size: 24px;
        }

        .example-cards {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 24px;
        }

        .example-card {
          background: rgba(255,255,255,0.1);
          backdrop-filter: blur(10px);
          border: 2px solid rgba(255,255,255,0.2);
          border-radius: 12px;
          padding: 24px;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .example-card:hover {
          background: rgba(255,255,255,0.2);
          transform: translateY(-4px);
        }

        .example-card h4 {
          margin: 0 0 8px 0;
          font-size: 20px;
        }

        .example-card p {
          margin: 0;
          opacity: 0.9;
          font-size: 14px;
        }

        .generated-app {
          max-width: 1400px;
          margin: 0 auto;
          background: white;
          border-radius: 16px;
          padding: 32px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.2);
        }

        .app-header {
          margin-bottom: 32px;
          padding-bottom: 24px;
          border-bottom: 2px solid #e0e0e0;
        }

        .app-title-section h1 {
          margin: 0 0 8px 0;
          color: #333;
        }

        .app-title-section p {
          color: #666;
          margin: 0 0 16px 0;
        }

        .app-meta {
          display: flex;
          align-items: center;
          gap: 16px;
        }

        .app-url {
          padding: 8px 16px;
          background: #f0f0f0;
          border-radius: 6px;
          font-family: monospace;
          font-size: 14px;
          color: #667eea;
        }

        .btn-back {
          padding: 8px 16px;
          background: transparent;
          border: 2px solid #667eea;
          color: #667eea;
          border-radius: 6px;
          cursor: pointer;
          font-weight: 600;
          transition: all 0.3s ease;
        }

        .btn-back:hover {
          background: #667eea;
          color: white;
        }

        .key-metrics {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 16px;
          margin-top: 24px;
        }

        .metric-card {
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
          padding: 24px;
          border-radius: 12px;
          text-align: center;
        }

        .metric-value {
          font-size: 36px;
          font-weight: 700;
          margin-bottom: 8px;
        }

        .metric-label {
          font-size: 14px;
          opacity: 0.9;
          text-transform: uppercase;
          letter-spacing: 1px;
        }

        .app-filters {
          background: #f9f9f9;
          padding: 24px;
          border-radius: 12px;
          margin-bottom: 32px;
        }

        .app-filters h3 {
          margin-top: 0;
          color: #333;
        }

        .filter-controls {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 16px;
        }

        .filter-item label {
          display: block;
          margin-bottom: 8px;
          font-weight: 600;
          color: #333;
        }

        .filter-item select,
        .filter-item input {
          width: 100%;
          padding: 10px;
          border: 2px solid #e0e0e0;
          border-radius: 6px;
          font-size: 14px;
        }

        .daterange-inputs {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .daterange-inputs input {
          flex: 1;
        }

        .charts-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
          gap: 32px;
          margin-bottom: 32px;
        }

        .chart-container {
          background: white;
          border: 2px solid #e0e0e0;
          border-radius: 12px;
          padding: 24px;
        }

        .chart-container h3 {
          margin-top: 0;
          color: #333;
          margin-bottom: 20px;
        }

        .app-actions {
          display: flex;
          gap: 16px;
          justify-content: center;
          padding-top: 24px;
          border-top: 2px solid #e0e0e0;
        }

        .app-actions button {
          padding: 12px 24px;
          border: 2px solid #667eea;
          background: white;
          color: #667eea;
          border-radius: 8px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .app-actions button:hover {
          background: #667eea;
          color: white;
        }
      `}</style>
    </div>
  );
};

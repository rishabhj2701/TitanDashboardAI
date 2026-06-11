// @ts-nocheck
import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  RadialLinearScale,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { Bar, Line, Pie, Scatter, Doughnut, PolarArea, Radar, Bubble } from 'react-chartjs-2';
import { getWebsiteByName } from './api/websiteClient';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  RadialLinearScale,
  Title,
  Tooltip,
  Legend,
  Filler
);

interface GeneratedSiteData {
  site_id: string;
  name: string;
  description: string;
  url: string;
  plan: any;
  ui_design: any;
  data_insights: any;
  charts: any[];
  code: any;
  data_columns: string[];
  sample_data: any[];
  dataset_stats: any;
  created_at: number;
  header?: {
    title?: string;
    subtitle?: string;
    style?: {
      background?: string;
    };
  };
}

// Helper function to generate chart data from actual uploaded data
// Explicit any return keeps Chart.js typings from rejecting mixed payload shapes
const generateRealChartData = (chart: any, sample_data: any[]): any => {
  const colors = ['#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b', '#fa709a', '#ff6b6b', '#4ecdc4', '#ffe66d', '#a8e6cf'];

  // Convert unsupported chart types to supported ones
  if (chart.type === 'map') {
    console.log(`  ⚠️ Converting 'map' chart to 'scatter' chart`);
    chart.type = 'scatter';
  }
  if (chart.type === 'area') {
    console.log(`  ⚠️ Converting 'area' chart to 'line' chart`);
    chart.type = 'line';
  }

  console.log(`📊 Generating chart: ${chart.title}`);
  console.log(`  Type: ${chart.type}`);
  console.log(`  X Column: ${chart.x_column}`);
  console.log(`  Y Column: ${chart.y_column}`);
  console.log(`  Sample data rows: ${sample_data?.length || 0}`);

  // Return null if no data available
  if (!sample_data || sample_data.length === 0) {
    console.log(`  ❌ No sample data available`);
    return null;
  }

  // Check if columns exist in data
  if (sample_data.length > 0) {
    const firstRow = sample_data[0];
    console.log(`  Available columns in data:`, Object.keys(firstRow));
    console.log(`  X column exists?`, chart.x_column in firstRow);
    console.log(`  Y column exists?`, chart.y_column in firstRow);
    console.log(`  Sample values - X:`, firstRow[chart.x_column], 'Y:', firstRow[chart.y_column]);
  }

  const xColumn = chart.x_column;
  const yColumn = chart.y_column;
  const aggregation = chart.aggregation || 'count';

  // For pie, doughnut, and polarArea charts, we typically want to show distribution of categories
  if (chart.type === 'pie' || chart.type === 'doughnut' || chart.type === 'polarArea') {
    // Group by x_column and aggregate
    const grouped: { [key: string]: number } = {};

    sample_data.forEach((row: any) => {
      const key = String(row[xColumn] || 'Unknown');

      if (aggregation === 'count') {
        grouped[key] = (grouped[key] || 0) + 1;
      } else if (aggregation === 'sum' && yColumn) {
        grouped[key] = (grouped[key] || 0) + (parseFloat(row[yColumn]) || 0);
      } else if (aggregation === 'avg' && yColumn) {
        // For avg, we'll need to track sum and count separately
        // Simplified: just use sum for now
        grouped[key] = (grouped[key] || 0) + (parseFloat(row[yColumn]) || 0);
      }
    });

    // Sort by value and take top 10
    const sortedEntries = Object.entries(grouped)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 10);

    return {
      labels: sortedEntries.map(([key]) => key),
      datasets: [{
        data: sortedEntries.map(([, value]) => value),
        backgroundColor: colors,
        borderWidth: 2,
      }]
    } as any;
  }

  // For bar and line charts
  if (chart.type === 'bar' || chart.type === 'line') {
    // Group by x_column and aggregate y_column
    const grouped: { [key: string]: { sum: number; count: number } } = {};

    sample_data.forEach((row: any) => {
      const key = String(row[xColumn] || 'Unknown');

      if (!grouped[key]) {
        grouped[key] = { sum: 0, count: 0 };
      }

      if (aggregation === 'count') {
        grouped[key].count += 1;
      } else if (yColumn) {
        const value = parseFloat(row[yColumn]);
        if (!isNaN(value)) {
          grouped[key].sum += value;
          grouped[key].count += 1;
        }
      }
    });

    // Calculate final values based on aggregation
    const processedData: { [key: string]: number } = {};
    Object.entries(grouped).forEach(([key, { sum, count }]) => {
      if (aggregation === 'count') {
        processedData[key] = count;
      } else if (aggregation === 'sum') {
        processedData[key] = sum;
      } else if (aggregation === 'avg') {
        processedData[key] = count > 0 ? sum / count : 0;
      } else if (aggregation === 'min' || aggregation === 'max') {
        // For min/max, we'd need to track differently, but for now use sum
        processedData[key] = sum;
      } else {
        processedData[key] = sum;
      }
    });

    // Sort by key and limit to top 20 for readability
    const sortedEntries = Object.entries(processedData)
      .sort(([a], [b]) => {
        // Try to sort numerically if possible, otherwise alphabetically
        const numA = parseFloat(a);
        const numB = parseFloat(b);
        if (!isNaN(numA) && !isNaN(numB)) {
          return numA - numB;
        }
        return a.localeCompare(b);
      })
      .slice(0, 20);

    return {
      labels: sortedEntries.map(([key]) => key),
      datasets: [{
        label: chart.title || yColumn || 'Value',
        data: sortedEntries.map(([, value]) => value),
        backgroundColor: chart.type === 'bar' ? colors[0] : 'transparent',
        borderColor: colors[0],
        borderWidth: 2,
        fill: chart.type === 'line' ? false : true,
      }]
    } as any;
  }

  // For scatter charts
  if (chart.type === 'scatter') {
    const scatterData = sample_data
      .filter((row: any) => row[xColumn] !== undefined && row[yColumn] !== undefined)
      .map((row: any) => ({
        x: parseFloat(row[xColumn]) || 0,
        y: parseFloat(row[yColumn]) || 0
      }))
      .slice(0, 100); // Limit to 100 points for performance

    if (scatterData.length === 0) {
      return null;
    }

    return {
      datasets: [{
        label: chart.title,
        data: scatterData,
        backgroundColor: colors[0] + '80',
        borderColor: colors[0],
        borderWidth: 1,
        pointRadius: 5,
      }]
    } as any;
  }

  // For heatmap, convert to bar chart (heatmap requires additional library)
  if (chart.type === 'heatmap') {
    // Treat heatmap as a grouped bar chart
    const grouped: { [key: string]: number } = {};

    sample_data.forEach((row: any) => {
      const key = String(row[xColumn] || 'Unknown');
      if (aggregation === 'count') {
        grouped[key] = (grouped[key] || 0) + 1;
      }
    });

    const sortedEntries = Object.entries(grouped)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 20);

    if (sortedEntries.length === 0) {
      return null;
    }

    return {
      labels: sortedEntries.map(([key]) => key),
      datasets: [{
        label: chart.title,
        data: sortedEntries.map(([, value]) => value),
        backgroundColor: colors.slice(0, sortedEntries.length),
        borderWidth: 2,
      }]
    } as any;
  }

  return null;
};

export const GeneratedSite: React.FC = () => {
  const { siteName } = useParams<{ siteName: string }>();
  const [siteData, setSiteData] = useState<GeneratedSiteData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSiteData = async () => {
      try {
        console.log('🔍 Looking for site:', siteName);

        // Always fetch from backend for fresh data (especially important for edits)
        console.log('🌐 Fetching fresh data from backend...');
        if (!siteName) {
          throw new Error('Missing website name in route.');
        }
        const data = await getWebsiteByName(siteName);
        setSiteData(data as unknown as GeneratedSiteData);
        setLoading(false);
      } catch (err) {
        console.error('❌ Error loading site:', err);
        setError(err instanceof Error ? err.message : 'Failed to load website');
        setLoading(false);
      }
    };

    fetchSiteData();
  }, [siteName]);

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        color: 'white'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '48px', marginBottom: '20px' }}>🔮</div>
          <div style={{ fontSize: '24px' }}>Loading {siteName}...</div>
        </div>
      </div>
    );
  }

  if (error || !siteData) {
    return (
      <div style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        color: 'white',
        padding: '40px'
      }}>
        <div style={{
          background: 'rgba(255,255,255,0.1)',
          borderRadius: '16px',
          padding: '60px',
          maxWidth: '600px',
          textAlign: 'center'
        }}>
          <div style={{ fontSize: '64px', marginBottom: '24px' }}>🚧</div>
          <h1 style={{ fontSize: '32px', marginBottom: '16px' }}>Website Not Found</h1>
          <p style={{ fontSize: '18px', marginBottom: '32px', opacity: 0.9 }}>
            The website "{siteName}" could not be loaded.
          </p>
          <p style={{ fontSize: '16px', marginBottom: '32px', opacity: 0.8 }}>
            {error}
          </p>
          <a
            href="/website-builder"
            style={{
              display: 'inline-block',
              padding: '16px 32px',
              background: 'white',
              color: '#667eea',
              textDecoration: 'none',
              borderRadius: '8px',
              fontWeight: 600,
              fontSize: '16px'
            }}
          >
            ← Back to Website Builder
          </a>
        </div>
      </div>
    );
  }

  return (
    <div style={{
      minHeight: '100vh',
      height: '100vh',
      overflowY: 'auto',
      background: siteData.ui_design?.color_scheme?.background || 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
      padding: '40px',
      marginTop: '60px'
    }}>
      <div style={{
        maxWidth: '1400px',
        margin: '0 auto',
        background: 'white',
        borderRadius: '16px',
        padding: '40px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        marginBottom: '40px'
      }}>
        {/* Header */}
        <div style={{
          borderBottom: `3px solid ${siteData.ui_design?.color_scheme?.primary || '#667eea'}`,
          paddingBottom: '24px',
          marginBottom: '32px'
        }}>
          <h1 style={{
            fontSize: '36px',
            fontWeight: 'bold',
            color: siteData.ui_design?.color_scheme?.primary || siteData.header?.style?.background || '#667eea',
            marginBottom: '12px'
          }}>
            {siteData.header?.title || siteData.name}
          </h1>
          <p style={{
            fontSize: '18px',
            color: '#666',
            marginBottom: '16px'
          }}>
            {siteData.header?.subtitle || siteData.description}
          </p>
          <div style={{
            display: 'flex',
            gap: '12px',
            flexWrap: 'wrap'
          }}>
            {siteData.plan?.key_features?.map((feature: string, idx: number) => (
              <span key={idx} style={{
                background: `${siteData.ui_design?.color_scheme?.primary || '#667eea'}20`,
                color: siteData.ui_design?.color_scheme?.primary || '#667eea',
                padding: '6px 16px',
                borderRadius: '20px',
                fontSize: '14px',
                fontWeight: 500
              }}>
                {feature}
              </span>
            ))}
          </div>
        </div>

        {/* Dataset Summary */}
        {siteData.data_insights?.dataset_summary && (
          <div style={{
            background: 'linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '32px',
            border: '2px solid #e0e0e0'
          }}>
            <h2 style={{
              fontSize: '20px',
              fontWeight: 'bold',
              marginBottom: '12px',
              color: '#333',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}>
              <span>📊</span> About This Dataset
            </h2>
            <p style={{
              fontSize: '16px',
              color: '#555',
              lineHeight: '1.6'
            }}>
              {siteData.data_insights.dataset_summary}
            </p>
          </div>
        )}

        {/* Data Insights */}
        {siteData.data_insights?.insights && siteData.data_insights.insights.length > 0 && (
          <div style={{
            background: '#f8f9fa',
            borderRadius: '12px',
            padding: '24px',
            marginBottom: '32px',
            border: '2px solid #e9ecef'
          }}>
            <h2 style={{
              fontSize: '20px',
              fontWeight: 'bold',
              marginBottom: '16px',
              color: '#333',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}>
              <span>💡</span> Key Insights
            </h2>
            <ul style={{
              listStyle: 'none',
              padding: 0,
              margin: 0
            }}>
              {siteData.data_insights.insights.map((insight: string, idx: number) => (
                <li key={idx} style={{
                  fontSize: '15px',
                  color: '#555',
                  marginBottom: '12px',
                  paddingLeft: '24px',
                  position: 'relative',
                  lineHeight: '1.5'
                }}>
                  <span style={{
                    position: 'absolute',
                    left: 0,
                    color: siteData.ui_design?.color_scheme?.primary || '#667eea',
                    fontWeight: 'bold'
                  }}>•</span>
                  {insight}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Key Metrics */}
        {siteData.data_insights?.key_metrics && siteData.data_insights.key_metrics.length > 0 && (
          <div style={{ marginBottom: '32px' }}>
            <h2 style={{
              fontSize: '24px',
              fontWeight: 'bold',
              marginBottom: '16px',
              color: '#333'
            }}>
              Key Metrics
            </h2>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: '16px'
            }}>
              {siteData.data_insights.key_metrics.map((metric: any, idx: number) => (
                <div key={idx} style={{
                  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                  padding: '24px',
                  borderRadius: '12px',
                  color: 'white'
                }}>
                  <div style={{ fontSize: '14px', opacity: 0.9, marginBottom: '8px' }}>
                    {typeof metric === 'string' ? metric : metric.name || 'Metric'}
                  </div>
                  <div style={{ fontSize: '28px', fontWeight: 'bold' }}>
                    {typeof metric === 'string' ? '—' : metric.value || '—'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Charts */}
        {siteData.charts && siteData.charts.length > 0 && (
          <div style={{ marginBottom: '32px' }}>
            <h2 style={{
              fontSize: '24px',
              fontWeight: 'bold',
              marginBottom: '16px',
              color: '#333'
            }}>
              Visualizations
            </h2>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
              gap: '20px'
            }}>
              {siteData.charts.map((chart: any, idx: number) => {
                const chartData = generateRealChartData(chart, siteData.sample_data) as any;
                return (
                <div key={idx} style={{
                  background: 'linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)',
                  padding: '24px',
                  borderRadius: '12px',
                  border: '2px solid rgba(102, 126, 234, 0.2)',
                  boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
                  transition: 'transform 0.2s, box-shadow 0.2s'
                }}>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    marginBottom: '16px'
                  }}>
                    <div style={{
                      width: '40px',
                      height: '40px',
                      borderRadius: '8px',
                      background: siteData.ui_design?.color_scheme?.primary || '#667eea',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      marginRight: '12px',
                      fontSize: '20px'
                    }}>
                      📊
                    </div>
                    <h3 style={{
                      fontSize: '18px',
                      fontWeight: 'bold',
                      margin: 0,
                      color: '#2d3748'
                    }}>
                      {chart.title}
                    </h3>
                  </div>

                  {/* Chart Description */}
                  {chart.description && (
                    <p style={{
                      fontSize: '14px',
                      color: '#666',
                      marginBottom: '16px',
                      lineHeight: '1.5',
                      fontStyle: 'italic'
                    }}>
                      {chart.description}
                    </p>
                  )}

                  <div style={{
                    background: 'white',
                    padding: '20px',
                    borderRadius: '8px',
                    marginBottom: '12px'
                  }}>
                    {/* Render actual chart */}
                    <div style={{ marginBottom: '16px', height: '300px' }}>
                      {chart.type === 'bar' && chartData && (
                        <Bar
                          data={chartData}
                          options={{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false } }
                          }}
                        />
                      )}
                      {chart.type === 'line' && chartData && (
                        <Line
                          data={chartData}
                          options={{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false } }
                          }}
                        />
                      )}
                      {chart.type === 'pie' && chartData && (
                        <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Pie data={chartData} options={{ responsive: true, maintainAspectRatio: false }} />
                        </div>
                      )}
                      {chart.type === 'doughnut' && chartData && (
                        <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Doughnut data={chartData} options={{ responsive: true, maintainAspectRatio: false }} />
                        </div>
                      )}
                      {chart.type === 'scatter' && chartData && (
                        <Scatter
                          data={chartData}
                          options={{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false } },
                            scales: {
                              x: { title: { display: true, text: chart.x_column } },
                              y: { title: { display: true, text: chart.y_column } }
                            }
                          }}
                        />
                      )}
                      {chart.type === 'bubble' && chartData && (
                        <Bubble data={chartData} options={{ responsive: true, maintainAspectRatio: false }} />
                      )}
                      {chart.type === 'radar' && chartData && (
                        <Radar data={chartData} options={{ responsive: true, maintainAspectRatio: false }} />
                      )}
                      {chart.type === 'polarArea' && chartData && (
                        <PolarArea data={chartData} options={{ responsive: true, maintainAspectRatio: false }} />
                      )}
                      {(chart.type === 'area' || chart.type === 'stacked-area') && chartData && (
                        <Line
                          data={chartData}
                          options={{
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false }, filler: { propagate: true } },
                            elements: { line: { fill: true } }
                          }}
                        />
                      )}
                      {(chart.type === 'heatmap' || chart.type === 'histogram' || chart.type === 'stacked-bar') && chartData && (
                        <Bar data={chartData} options={{ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }} />
                      )}
                      {(!['bar', 'line', 'pie', 'doughnut', 'scatter', 'bubble', 'radar', 'polarArea', 'area', 'stacked-area', 'heatmap', 'histogram', 'stacked-bar'].includes(chart.type) || !chartData) && (
                        <div style={{
                          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                          borderRadius: '8px',
                          padding: '80px 20px',
                          textAlign: 'center',
                          color: 'white',
                          fontSize: '14px',
                          fontWeight: 500,
                          height: '100%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexDirection: 'column'
                        }}>
                          📊 {chart.type.charAt(0).toUpperCase() + chart.type.slice(1)} Chart
                          <div style={{ fontSize: '12px', opacity: 0.9, marginTop: '8px' }}>
                            {!chartData
                              ? 'No data available for this chart'
                              : `Visualization type: ${chart.type}`}
                          </div>
                        </div>
                      )}
                    </div>
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      marginBottom: '8px',
                      padding: '8px',
                      background: '#f7fafc',
                      borderRadius: '6px'
                    }}>
                      <span style={{
                        color: '#667eea',
                        fontWeight: 600,
                        fontSize: '13px',
                        marginRight: '8px',
                        minWidth: '80px'
                      }}>Type:</span>
                      <span style={{
                        color: '#4a5568',
                        fontSize: '14px',
                        fontWeight: 500
                      }}>{chart.type}</span>
                    </div>
                    {chart.x_column && (
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        marginBottom: '8px',
                        padding: '8px',
                        background: '#f7fafc',
                        borderRadius: '6px'
                      }}>
                        <span style={{
                          color: '#667eea',
                          fontWeight: 600,
                          fontSize: '13px',
                          marginRight: '8px',
                          minWidth: '80px'
                        }}>X-Axis:</span>
                        <span style={{
                          color: '#4a5568',
                          fontSize: '14px',
                          fontWeight: 500
                        }}>{chart.x_column}</span>
                      </div>
                    )}
                    {chart.y_column && (
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        marginBottom: '8px',
                        padding: '8px',
                        background: '#f7fafc',
                        borderRadius: '6px'
                      }}>
                        <span style={{
                          color: '#667eea',
                          fontWeight: 600,
                          fontSize: '13px',
                          marginRight: '8px',
                          minWidth: '80px'
                        }}>Y-Axis:</span>
                        <span style={{
                          color: '#4a5568',
                          fontSize: '14px',
                          fontWeight: 500
                        }}>{chart.y_column}</span>
                      </div>
                    )}
                    {chart.aggregation && (
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        padding: '8px',
                        background: '#f7fafc',
                        borderRadius: '6px'
                      }}>
                        <span style={{
                          color: '#667eea',
                          fontWeight: 600,
                          fontSize: '13px',
                          marginRight: '8px',
                          minWidth: '80px'
                        }}>Aggregation:</span>
                        <span style={{
                          color: '#4a5568',
                          fontSize: '14px',
                          fontWeight: 500
                        }}>{chart.aggregation}</span>
                      </div>
                    )}
                  </div>
                </div>
              )})}
            </div>
          </div>
        )}

        {/* Data Insights */}
        {siteData.data_insights?.insights && siteData.data_insights.insights.length > 0 && (
          <div style={{ marginBottom: '32px' }}>
            <h2 style={{
              fontSize: '24px',
              fontWeight: 'bold',
              marginBottom: '16px',
              color: '#333'
            }}>
              Data Insights
            </h2>
            <div style={{
              background: '#f8f9fa',
              padding: '24px',
              borderRadius: '12px',
              border: '1px solid #e0e0e0'
            }}>
              <ul style={{ margin: 0, paddingLeft: '24px' }}>
                {siteData.data_insights.insights.map((insight: any, idx: number) => (
                  <li key={idx} style={{
                    fontSize: '16px',
                    color: '#444',
                    marginBottom: '12px',
                    lineHeight: '1.6'
                  }}>
                    {typeof insight === 'string' ? insight : insight.description || insight.text || JSON.stringify(insight)}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {/* Data Columns */}
        {siteData.data_columns && siteData.data_columns.length > 0 && (
          <div>
            <h2 style={{
              fontSize: '24px',
              fontWeight: 'bold',
              marginBottom: '16px',
              color: '#333'
            }}>
              Available Data
            </h2>
            <div style={{
              background: '#f8f9fa',
              padding: '24px',
              borderRadius: '12px',
              border: '1px solid #e0e0e0'
            }}>
              <div style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '12px'
              }}>
                {siteData.data_columns.map((column: string, idx: number) => (
                  <span key={idx} style={{
                    background: 'white',
                    padding: '8px 16px',
                    borderRadius: '6px',
                    fontSize: '14px',
                    border: '1px solid #ddd',
                    fontFamily: 'monospace',
                    color: '#667eea'
                  }}>
                    {column}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

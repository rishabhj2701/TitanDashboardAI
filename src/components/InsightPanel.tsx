import { useState, useEffect, useCallback } from 'react';
import { getSummaryStats, getSpeedCompliance, getTopSpeedingRoads } from '../api/dataQualityClient';

interface Insight {
  id: string;
  icon: string;
  text: string;
  severity: 'info' | 'warning' | 'critical';
}

function InsightPanel() {
  const [open, setOpen] = useState(false);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [healthScore, setHealthScore] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  const loadInsights = useCallback(async () => {
    setLoading(true);
    const results: Insight[] = [];
    try {
      const [stats, compliance, topSpeeding] = await Promise.all([
        getSummaryStats().catch(() => null),
        getSpeedCompliance().catch(() => null),
        getTopSpeedingRoads(5).catch(() => null),
      ]);

      // Network Health Score
      if (compliance) {
        const total = (compliance.within_limit || 0) + (compliance.over_limit || 0) + (compliance.no_limit_data || 0);
        if (total > 0) {
          const score = Math.round(((compliance.within_limit || 0) / total) * 100);
          setHealthScore(score);
          if (score < 50) {
            results.push({ id: 'health-low', icon: '\u26A0', text: `Network health is low (${score}%). Over half of measured roads exceed speed limits by 10+ mph.`, severity: 'critical' });
          } else if (score < 75) {
            results.push({ id: 'health-mod', icon: '\u26A0', text: `Network health is moderate (${score}%). Some roads show significant speed non-compliance.`, severity: 'warning' });
          } else {
            results.push({ id: 'health-good', icon: '\u2713', text: `Network health is good (${score}%). Most roads are within acceptable speed ranges.`, severity: 'info' });
          }

          if (compliance.over_limit > 0) {
            results.push({ id: 'over-limit', icon: '\u26A0', text: `${compliance.over_limit.toLocaleString()} road segments have avg speeds 10+ mph over the limit.`, severity: 'warning' });
          }
        }
      }

      if (stats) {
        if (stats.total > 0) {
          results.push({ id: 'total-points', icon: '\u2139', text: `Dataset contains ${stats.total.toLocaleString()} data points across the road network.`, severity: 'info' });
        }
        if (stats.avgSpeed > 0 && stats.maxSpeed > 0) {
          results.push({ id: 'speed-range', icon: '\u2139', text: `Network average speed is ${stats.avgSpeed} mph, with a maximum of ${stats.maxSpeed} mph recorded.`, severity: 'info' });
        }
      }

      if (topSpeeding) {
        const roads = topSpeeding.roads || [];
        if (roads.length > 0) {
          const worst = roads[0];
          const overBy = worst.speed_over_limit?.toFixed(1) || '?';
          results.push({ id: 'worst-road', icon: '\u26A0', text: `${worst.road_name || 'Unknown road'} has the highest speed excess at ${overBy} mph over the limit.`, severity: 'critical' });
        }
        if (roads.length >= 3) {
          const names = roads.slice(0, 3).map(r => r.road_name || 'Unknown').join(', ');
          results.push({ id: 'top3-speeding', icon: '\u26A0', text: `Top speeding corridors: ${names}`, severity: 'warning' });
        }
      }

      // Generate summary
      const summaryParts: string[] = [];
      if (stats?.total) summaryParts.push(`This CV run contains ${stats.total.toLocaleString()} data points.`);
      if (compliance) {
        const total = (compliance.within_limit || 0) + (compliance.over_limit || 0) + (compliance.no_limit_data || 0);
        if (total > 0) summaryParts.push(`${compliance.within_limit?.toLocaleString() || 0} segments (${Math.round(((compliance.within_limit || 0) / total) * 100)}%) are within speed limits.`);
        if (compliance.over_limit) summaryParts.push(`${compliance.over_limit.toLocaleString()} segments exceed limits by 10+ mph.`);
      }
      if (topSpeeding?.roads?.length) {
        summaryParts.push(`The worst speeding corridor is ${topSpeeding.roads[0].road_name || 'Unknown'} at ${topSpeeding.roads[0].speed_over_limit?.toFixed(1) || '?'} mph over limit.`);
      }
      if (summaryParts.length > 0) setSummary(summaryParts.join(' '));
    } catch (err) {
      console.error('Failed to load insights:', err);
    }
    setInsights(results);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (open && insights.length === 0 && !loading) {
      loadInsights();
    }
  }, [open, insights.length, loading, loadInsights]);

  const handleExportSummary = useCallback(() => {
    if (!summary) return;
    const blob = new Blob([`Traffic AI System - Executive Summary\n${'='.repeat(45)}\n\n${summary}\n\nGenerated: ${new Date().toLocaleString()}`], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.download = 'traffic_summary.txt';
    link.href = url;
    link.click();
    URL.revokeObjectURL(url);
  }, [summary]);

  const scoreColor = healthScore == null ? '#666' : healthScore >= 75 ? '#22c55e' : healthScore >= 50 ? '#f59e0b' : '#ef4444';

  return (
    <>
      <button
        className="insight-toggle-btn"
        onClick={() => setOpen(!open)}
        title="AI Insights & Network Health"
      >
        {healthScore != null ? (
          <span className="insight-score-badge" style={{ background: scoreColor }}>{healthScore}</span>
        ) : (
          <span className="insight-score-badge" style={{ background: '#444' }}>AI</span>
        )}
      </button>
      {open && (
        <div className="insight-panel">
          <div className="insight-panel-header">
            <span className="insight-panel-title">Network Intelligence</span>
            <button className="insight-panel-close" onClick={() => setOpen(false)}>{'\u2715'}</button>
          </div>

          {/* Health Score */}
          {healthScore != null && (
            <div className="insight-health-score">
              <div className="insight-health-ring" style={{ borderColor: scoreColor }}>
                <span className="insight-health-number" style={{ color: scoreColor }}>{healthScore}</span>
              </div>
              <div className="insight-health-label">
                <strong>Network Health</strong>
                <span>{healthScore >= 75 ? 'Good' : healthScore >= 50 ? 'Moderate' : 'Needs Attention'}</span>
              </div>
            </div>
          )}

          {loading && <div className="insight-loading">Analyzing network data...</div>}

          {/* Insight Cards */}
          <div className="insight-cards">
            {insights.map(i => (
              <div key={i.id} className={`insight-card insight-${i.severity}`}>
                <span className="insight-card-icon">{i.icon}</span>
                <span className="insight-card-text">{i.text}</span>
              </div>
            ))}
          </div>

          {/* Summary */}
          {summary && (
            <div className="insight-summary">
              <div className="insight-summary-title">Executive Summary</div>
              <p className="insight-summary-text">{summary}</p>
              <button className="insight-summary-export" onClick={handleExportSummary}>Export Summary</button>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default InsightPanel;

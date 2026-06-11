import { useState, useEffect, useCallback, useMemo } from 'react';
import { getTopSpeedingRoads, getSpeedCompliance, getSummaryStats } from '../api/dataQualityClient';
import type { TopSpeedingRoad } from '../api/dataQualityClient';

interface RoadReportCardProps {
  onBack: () => void;
  onToast?: (msg: string) => void;
}

function computeGrade(road: TopSpeedingRoad): { grade: string; color: string; score: number } {
  const over = road.speed_over_limit;
  if (over <= 0) return { grade: 'A', color: '#22c55e', score: 95 };
  if (over <= 3) return { grade: 'B', color: '#84cc16', score: 80 };
  if (over <= 7) return { grade: 'C', color: '#f59e0b', score: 65 };
  if (over <= 12) return { grade: 'D', color: '#f97316', score: 45 };
  return { grade: 'F', color: '#ef4444', score: 20 };
}

function GradeRing({ grade, color, score }: { grade: string; color: string; score: number }) {
  const circumference = 2 * Math.PI * 45;
  const offset = circumference - (score / 100) * circumference;
  return (
    <div className="rrc-grade-ring">
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle cx="60" cy="60" r="45" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="8" />
        <circle
          cx="60" cy="60" r="45" fill="none"
          stroke={color} strokeWidth="8" strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 60 60)"
          style={{ transition: 'stroke-dashoffset 0.8s ease' }}
        />
        <text x="60" y="55" textAnchor="middle" fill={color} fontSize="36" fontWeight="800">{grade}</text>
        <text x="60" y="75" textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize="11" fontWeight="600">{score}/100</text>
      </svg>
    </div>
  );
}

function StatBox({ label, value, unit, accent }: { label: string; value: string; unit?: string; accent?: string }) {
  return (
    <div className="rrc-stat-box" style={{ borderLeftColor: accent || 'rgba(100,255,218,0.3)' }}>
      <div className="rrc-stat-value">{value}{unit && <span className="rrc-stat-unit">{unit}</span>}</div>
      <div className="rrc-stat-label">{label}</div>
    </div>
  );
}

function SpeedBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="rrc-speed-bar">
      <div className="rrc-speed-bar-label">{label}</div>
      <div className="rrc-speed-bar-track">
        <div className="rrc-speed-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="rrc-speed-bar-value">{value.toFixed(1)} mph</div>
    </div>
  );
}

function RoadReportCard({ onBack, onToast }: RoadReportCardProps) {
  const [roads, setRoads] = useState<TopSpeedingRoad[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [networkStats, setNetworkStats] = useState<{ total: number; avgSpeed: number; maxSpeed: number } | null>(null);
  const [compliance, setCompliance] = useState<{ within: number; over: number; noData: number } | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getTopSpeedingRoads(30).catch(() => null),
      getSummaryStats().catch(() => null),
      getSpeedCompliance().catch(() => null),
    ]).then(([topData, statsData, compData]) => {
      if (topData?.roads) setRoads(topData.roads);
      if (statsData) setNetworkStats({ total: statsData.total, avgSpeed: statsData.avgSpeed, maxSpeed: statsData.maxSpeed });
      if (compData) setCompliance({ within: compData.within_limit, over: compData.over_limit, noData: compData.no_limit_data });
      setLoading(false);
    });
  }, []);

  const selectedRoad = roads[selectedIdx] || null;
  const gradeInfo = selectedRoad ? computeGrade(selectedRoad) : null;
  const maxSpeed = useMemo(() => Math.max(...roads.map(r => r.avg_speed_mph), 1), [roads]);

  const handleExport = useCallback(() => {
    if (!selectedRoad || !gradeInfo) return;
    const lines = [
      `Road Report Card: ${selectedRoad.road_name}`,
      `${'='.repeat(50)}`,
      `Grade: ${gradeInfo.grade} (${gradeInfo.score}/100)`,
      `Average Speed: ${selectedRoad.avg_speed_mph.toFixed(1)} mph`,
      `Speed Limit: ${selectedRoad.speed_limit_mph.toFixed(1)} mph`,
      `Over Limit By: ${selectedRoad.speed_over_limit.toFixed(1)} mph`,
      `Data Points: ${selectedRoad.point_count.toLocaleString()}`,
      ``,
      `Generated: ${new Date().toLocaleString()}`,
    ];
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.download = `road_report_${selectedRoad.road_name.replace(/\s+/g, '_')}.txt`;
    a.href = url;
    a.click();
    URL.revokeObjectURL(url);
    onToast?.('Report exported');
  }, [selectedRoad, gradeInfo, onToast]);

  if (loading) {
    return (
      <div className="rrc-page">
        <div className="rrc-loading">Loading road data...</div>
      </div>
    );
  }

  if (roads.length === 0) {
    return (
      <div className="rrc-page">
        <div className="rrc-empty">
          <div className="rrc-empty-icon">{'\u{1F6E3}'}</div>
          <div className="rrc-empty-title">No Road Data Available</div>
          <div className="rrc-empty-desc">Load a CV run to see road report cards</div>
          <button className="rrc-back-btn" onClick={onBack}>Back to Map</button>
        </div>
      </div>
    );
  }

  return (
    <div className="rrc-page">
      {/* Header */}
      <div className="rrc-header">
        <button className="rrc-back-btn" onClick={onBack}>{'\u2190'} Back</button>
        <h1 className="rrc-title">Road Report Cards</h1>
        <button className="rrc-export-btn" onClick={handleExport}>Export Report</button>
      </div>

      <div className="rrc-layout">
        {/* Road List */}
        <div className="rrc-sidebar">
          <div className="rrc-sidebar-title">Roads ({roads.length})</div>
          <div className="rrc-road-list">
            {roads.map((road, idx) => {
              const g = computeGrade(road);
              return (
                <button
                  key={idx}
                  className={`rrc-road-item${idx === selectedIdx ? ' active' : ''}`}
                  onClick={() => setSelectedIdx(idx)}
                >
                  <span className="rrc-road-grade-mini" style={{ color: g.color }}>{g.grade}</span>
                  <span className="rrc-road-name">{road.road_name || 'Unknown'}</span>
                  <span className="rrc-road-speed-mini">+{road.speed_over_limit.toFixed(1)}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Main Report */}
        {selectedRoad && gradeInfo && (
          <div className="rrc-main">
            {/* Top: Grade + Road Name */}
            <div className="rrc-report-top">
              <GradeRing grade={gradeInfo.grade} color={gradeInfo.color} score={gradeInfo.score} />
              <div className="rrc-report-info">
                <h2 className="rrc-road-title">{selectedRoad.road_name || 'Unknown Road'}</h2>
                <div className="rrc-road-subtitle">
                  Speed Compliance Grade: <strong style={{ color: gradeInfo.color }}>{gradeInfo.grade}</strong>
                </div>
                <div className="rrc-road-detail">
                  {selectedRoad.point_count.toLocaleString()} data points analyzed
                </div>
              </div>
            </div>

            {/* Stats Grid */}
            <div className="rrc-stats-grid">
              <StatBox label="Avg Speed" value={selectedRoad.avg_speed_mph.toFixed(1)} unit=" mph" accent="#2196f3" />
              <StatBox label="Speed Limit" value={selectedRoad.speed_limit_mph.toFixed(1)} unit=" mph" accent="#22c55e" />
              <StatBox label="Over Limit" value={`+${selectedRoad.speed_over_limit.toFixed(1)}`} unit=" mph" accent={gradeInfo.color} />
              <StatBox label="Data Points" value={selectedRoad.point_count.toLocaleString()} accent="#9c27b0" />
            </div>

            {/* Speed Comparison Bar */}
            <div className="rrc-section">
              <div className="rrc-section-title">Speed Comparison</div>
              <div className="rrc-speed-bars">
                <SpeedBar label="This Road" value={selectedRoad.avg_speed_mph} max={maxSpeed} color={gradeInfo.color} />
                <SpeedBar label="Speed Limit" value={selectedRoad.speed_limit_mph} max={maxSpeed} color="#22c55e" />
                {networkStats && <SpeedBar label="Network Avg" value={networkStats.avgSpeed} max={maxSpeed} color="#2196f3" />}
              </div>
            </div>

            {/* Network Context */}
            {compliance && (
              <div className="rrc-section">
                <div className="rrc-section-title">Network Context</div>
                <div className="rrc-network-grid">
                  <div className="rrc-network-stat">
                    <div className="rrc-network-value" style={{ color: '#22c55e' }}>{compliance.within.toLocaleString()}</div>
                    <div className="rrc-network-label">Within Limit</div>
                  </div>
                  <div className="rrc-network-stat">
                    <div className="rrc-network-value" style={{ color: '#ef4444' }}>{compliance.over.toLocaleString()}</div>
                    <div className="rrc-network-label">Over Limit</div>
                  </div>
                  <div className="rrc-network-stat">
                    <div className="rrc-network-value" style={{ color: '#666' }}>{compliance.noData.toLocaleString()}</div>
                    <div className="rrc-network-label">No Limit Data</div>
                  </div>
                  {networkStats && (
                    <div className="rrc-network-stat">
                      <div className="rrc-network-value" style={{ color: '#64ffda' }}>{networkStats.total.toLocaleString()}</div>
                      <div className="rrc-network-label">Total Points</div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Ranking among all roads */}
            <div className="rrc-section">
              <div className="rrc-section-title">Ranking</div>
              <div className="rrc-ranking">
                <span className="rrc-ranking-num">#{selectedIdx + 1}</span>
                <span className="rrc-ranking-text">of {roads.length} worst speeding roads</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default RoadReportCard;

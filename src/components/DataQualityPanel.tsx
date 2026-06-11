import { useEffect } from 'react';
import { useDataQuality } from '../hooks/useDataQuality';
import './DataQualityPanel.css';

const FIELD_LABELS: Record<string, string> = {
  lat: 'Latitude',
  lon: 'Longitude',
  timestamp: 'Timestamp',
  speed: 'Speed',
  speed_limit: 'Speed Limit',
  road_name: 'Road Name',
  road_segment_id: 'Road Segment',
  road_conf: 'Road Match Conf.',
  road_dist_m: 'Road Match Dist.',
  accel_x: 'Acceleration X',
  accel_y: 'Acceleration Y',
  vehicle_id: 'Vehicle ID',
  county: 'County',
  func_class: 'Functional Class',
};

function completenessClass(pct: number): string {
  if (pct >= 0.9) return 'high';
  if (pct >= 0.5) return 'medium';
  return 'low';
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

interface DataQualityPanelProps {
  onClose?: () => void;
}

function DataQualityPanel({ onClose }: DataQualityPanelProps) {
  const { data, loading, error, refresh } = useDataQuality();

  useEffect(() => {
    if (!data && !loading && !error) {
      refresh();
    }
  }, [data, loading, error, refresh]);

  const overallPct = data ? Math.round(data.overall_completeness * 100) : 0;
  const circumference = 2 * Math.PI * 28;
  const dashOffset = circumference - (circumference * overallPct) / 100;

  return (
    <div className="dq-panel">
      <div className="dq-header">
        <div className="dq-header-left">
          <span className="dq-title">Data Quality</span>
          <button
            className="dq-refresh-btn"
            onClick={refresh}
            disabled={loading}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
        {onClose && (
          <button className="dq-refresh-btn" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      {loading && !data && (
        <div className="dq-loading">Loading data quality metrics...</div>
      )}

      {error && (
        <div className="dq-error">{error}</div>
      )}

      {data && data.total_rows === 0 && (
        <div className="dq-empty">No CV data loaded. Select a CV run to view data quality.</div>
      )}

      {data && data.total_rows > 0 && (
        <>
          <div className="dq-score-section">
            <svg className="dq-score-ring" width="70" height="70" viewBox="0 0 70 70">
              <circle className="dq-score-ring-bg" cx="35" cy="35" r="28" />
              <circle
                className="dq-score-ring-fill"
                cx="35"
                cy="35"
                r="28"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                transform="rotate(-90 35 35)"
              />
              <text className="dq-score-text" x="35" y="35" textAnchor="middle" dominantBaseline="central">
                {overallPct}%
              </text>
            </svg>
            <div className="dq-score-details">
              <span className="dq-score-label">Overall Completeness</span>
              <span className="dq-score-value">{overallPct}%</span>
              <span className="dq-score-rows">{formatNumber(data.total_rows)} total rows</span>
            </div>
          </div>

          <table className="dq-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Completeness</th>
                <th></th>
                <th>Null</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.fields).map(([fieldName, field]) => {
                const pct = Math.round(field.completeness * 100);
                return (
                  <tr key={fieldName}>
                    <td className="dq-field-name">{FIELD_LABELS[fieldName] || fieldName}</td>
                    <td>
                      <div className="dq-bar">
                        <div
                          className={`dq-bar-fill ${completenessClass(field.completeness)}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </td>
                    <td className="dq-pct">{pct}%</td>
                    <td className="dq-null-count">{formatNumber(field.null_count)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default DataQualityPanel;

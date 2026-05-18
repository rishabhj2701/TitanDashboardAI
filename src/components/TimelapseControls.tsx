import { useState, useCallback, useRef, useEffect } from 'react';
import { getHourlyTrend } from '../api/dataQualityClient';

interface TimelapseControlsProps {
  mapRef: React.RefObject<mapboxgl.Map | null>;
  mapReady: boolean;
}

const HOUR_LABELS = ['12AM', '1AM', '2AM', '3AM', '4AM', '5AM', '6AM', '7AM', '8AM', '9AM', '10AM', '11AM', '12PM', '1PM', '2PM', '3PM', '4PM', '5PM', '6PM', '7PM', '8PM', '9PM', '10PM', '11PM'];

function TimelapseControls({ mapRef, mapReady }: TimelapseControlsProps) {
  const [open, setOpen] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [hour, setHour] = useState(0);
  const [hourlyData, setHourlyData] = useState<{ hour: number; avg_speed: number; point_count: number }[]>([]);
  const intervalRef = useRef<number>(0);

  useEffect(() => {
    if (open && hourlyData.length === 0) {
      getHourlyTrend().then(res => {
        if (res?.hours) setHourlyData(res.hours);
      }).catch(() => {});
    }
  }, [open, hourlyData.length]);

  const handlePlay = useCallback(() => {
    if (playing) {
      clearInterval(intervalRef.current);
      setPlaying(false);
      return;
    }
    setPlaying(true);
    intervalRef.current = window.setInterval(() => {
      setHour(prev => (prev + 1) % 24);
    }, 1500);
  }, [playing]);

  const handleSliderChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setHour(parseInt(e.target.value));
  }, []);

  useEffect(() => {
    return () => clearInterval(intervalRef.current);
  }, []);

  // Update map road colors based on hour's speed
  useEffect(() => {
    if (!mapReady || !mapRef.current || hourlyData.length === 0) return;
    const map = mapRef.current;
    const hourData = hourlyData.find(h => h.hour === hour);
    if (!hourData) return;

    // Adjust road opacity based on the hour's activity level
    const maxCount = Math.max(...hourlyData.map(h => h.point_count));
    const ratio = maxCount > 0 ? hourData.point_count / maxCount : 0.5;
    try {
      if (map.getLayer('cv-road-lines')) {
        map.setPaintProperty('cv-road-lines', 'line-opacity', 0.3 + ratio * 0.7);
      }
    } catch { /* layer may not exist */ }
  }, [hour, hourlyData, mapRef, mapReady]);

  const currentData = hourlyData.find(h => h.hour === hour);
  const maxCount = hourlyData.length > 0 ? Math.max(...hourlyData.map(h => h.point_count)) : 0;

  if (!open) {
    return (
      <button className="timelapse-toggle" onClick={() => setOpen(true)} title="Time-Lapse Playback">
        {'\u23F1'}
      </button>
    );
  }

  return (
    <div className="timelapse-panel">
      <div className="timelapse-header">
        <span className="timelapse-title">Time-Lapse</span>
        <button className="timelapse-close" onClick={() => { setOpen(false); setPlaying(false); clearInterval(intervalRef.current); }}>{'\u2715'}</button>
      </div>

      {/* Mini bar chart showing hourly activity */}
      <div className="timelapse-chart">
        {Array.from({ length: 24 }, (_, i) => {
          const d = hourlyData.find(h => h.hour === i);
          const height = d && maxCount > 0 ? (d.point_count / maxCount) * 100 : 2;
          return (
            <div
              key={i}
              className={`timelapse-bar${i === hour ? ' active' : ''}`}
              style={{ height: `${Math.max(height, 2)}%` }}
              onClick={() => setHour(i)}
              title={`${HOUR_LABELS[i]}: ${d?.point_count?.toLocaleString() || 0} pts, ${d?.avg_speed?.toFixed(1) || '?'} mph`}
            />
          );
        })}
      </div>

      <div className="timelapse-controls">
        <button className={`timelapse-play${playing ? ' playing' : ''}`} onClick={handlePlay}>
          {playing ? '\u23F8' : '\u25B6'}
        </button>
        <input
          type="range"
          min={0}
          max={23}
          value={hour}
          onChange={handleSliderChange}
          className="timelapse-slider"
        />
        <span className="timelapse-hour">{HOUR_LABELS[hour]}</span>
      </div>

      {currentData && (
        <div className="timelapse-stats">
          <span>{currentData.point_count.toLocaleString()} data points</span>
          <span>{currentData.avg_speed.toFixed(1)} mph avg</span>
        </div>
      )}
    </div>
  );
}

export default TimelapseControls;

import type { CSSProperties, PointerEventHandler, RefObject } from 'react';

type PanelStats = {
  total: number;
  cvPoints: number;
  topRoadName: string;
  topRoadUniqueVehicles: number;
  topRoadAvgUniqueVehiclesPerHour: number | null;
  avgSpeed: number;
  maxSpeed: number;
};

type StatsOverlayProps = {
  open: boolean;
  statsPanelRef: RefObject<HTMLDivElement | null>;
  statsPanelPos: { x: number; y: number } | null;
  statsPanelScale: number;
  panelStats: PanelStats;
  statsBboxLabel: string | null;
  secondaryLabel: string;
  onClose: () => void;
  onDragStart: PointerEventHandler<HTMLDivElement>;
  onDragMove: PointerEventHandler<HTMLDivElement>;
  onDragEnd: PointerEventHandler<HTMLDivElement>;
};

function StatsOverlay({
  open,
  statsPanelRef,
  statsPanelPos,
  statsPanelScale,
  panelStats,
  statsBboxLabel,
  secondaryLabel,
  onClose,
  onDragStart,
  onDragMove,
  onDragEnd,
}: StatsOverlayProps) {
  if (!open) return null;

  const overlayStyle: CSSProperties | undefined = statsPanelPos
    ? {
        left: `${statsPanelPos.x}px`,
        top: `${statsPanelPos.y}px`,
      }
    : undefined;
  if (overlayStyle) {
    (overlayStyle as CSSProperties & Record<'--stats-scale', string>)['--stats-scale'] = String(statsPanelScale);
  }
  const topRoadAvgUniqueVehiclesPerHourLabel = Number.isFinite(Number(panelStats.topRoadAvgUniqueVehiclesPerHour))
    ? Number(panelStats.topRoadAvgUniqueVehiclesPerHour).toFixed(1)
    : '--';
  const topRoadName = panelStats.topRoadName || 'ROAD (SEG ID)';
  const uniqueLabel = `Unique Vehicles on ${topRoadName}`;
  const avgLabel = `Unique Veh/Hr on ${topRoadName}`;

  return (
    <div
      ref={statsPanelRef}
      className="stats-overlay stats-overlay-square"
      style={overlayStyle}
    >
      <div
        className="stats-header"
        onPointerDown={onDragStart}
        onPointerMove={onDragMove}
        onPointerUp={onDragEnd}
        onPointerCancel={onDragEnd}
      >
        <div className="stats-header-text">
          <div className="stats-title">Statistics</div>
          {statsBboxLabel && (
            <div className="stats-bbox-label">{statsBboxLabel}</div>
          )}
        </div>
        <button
          type="button"
          className="chat-close-btn stats-close-btn"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onClose}
          aria-label="Close statistics panel"
        >
          X
        </button>
      </div>
      <div className="stats-grid stats-grid-square">
        <div className="stat-item">
          <span className="stat-value">{panelStats.total.toLocaleString()}</span>
          <span className="stat-label">Total Points</span>
        </div>
        <div className="stat-item cv">
          <span className="stat-value">{panelStats.cvPoints.toLocaleString()}</span>
          <span className="stat-label">{secondaryLabel}</span>
        </div>
        <div className="stat-item cv">
          <span className="stat-value">{panelStats.topRoadUniqueVehicles.toLocaleString()}</span>
          <span className="stat-label stat-label-detail">{uniqueLabel}</span>
        </div>
        <div className="stat-item speed">
          <span className="stat-value">{panelStats.avgSpeed} <small>mph</small></span>
          <span className="stat-label">Avg Speed</span>
        </div>
        <div className="stat-item speed">
          <span className="stat-value">{panelStats.maxSpeed} <small>mph</small></span>
          <span className="stat-label">Max Speed</span>
        </div>
        <div className="stat-item cv">
          <span className="stat-value">{topRoadAvgUniqueVehiclesPerHourLabel}</span>
          <span className="stat-label stat-label-detail">{avgLabel}</span>
        </div>
      </div>
    </div>
  );
}

export default StatsOverlay;
